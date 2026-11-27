"""Command-line entry point: `rib`."""

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from release_impact_brief.adapters import Extraction
from release_impact_brief.facts import EdgeKind, FactStore, NodeKind
from release_impact_brief.fetch import FetchError, FetchResult, fetch_repo
from release_impact_brief.llm import (
    CostUnknown,
    LlmAuthError,
    LlmError,
    LlmSession,
    LlmUnavailable,
    Settings,
    SpendingCapExceeded,
    load_settings,
    make_provider,
)
from release_impact_brief.pipeline import analyze
from release_impact_brief.summaries import Summarizer, SummaryCache, SummaryResult
from release_impact_brief.system import classify, unclear_items
from release_impact_brief.views import save_view


def _fetch(args: argparse.Namespace) -> int:
    with fetch_repo(args.url, args.ref) as result:
        print(f"repo:        {result.repo.url}")
        print(f"commit:      {result.commit_sha}")
        print(f"files:       {result.file_count}")
        print(f"size:        {result.total_bytes / 1024:.1f} KB")
        if result.skipped_large:
            print(f"skipped:     {len(result.skipped_large)} files over the size limit")
        print("by extension:")
        for ext, count in result.by_extension.most_common(10):
            print(f"  {ext:<12} {count}")
    return 0


def _load(args: argparse.Namespace) -> tuple[FactStore, int]:
    """Fetch, extract and store all facts for the repo; returns the store and snapshot."""
    # Always re-extract: parsing takes about a second, and a cache keyed only by commit
    # would serve stale facts after the extractor changes. Only LLM output gets cached.
    with fetch_repo(args.url, args.ref) as fetched:
        extraction = analyze(fetched.path, fetched.skipped_large)
    return _store(args, fetched, extraction)


def _store(args: argparse.Namespace, fetched: FetchResult,
           extraction: Extraction) -> tuple[FactStore, int]:  # fmt: skip
    args.db.parent.mkdir(parents=True, exist_ok=True)
    store = FactStore(args.db)
    snap = store.snapshot(fetched.repo.url, fetched.commit_sha)
    store.replace_facts(snap, extraction.nodes, extraction.edges)
    print(f"parsed:      {extraction.files_parsed} files")
    for rel in extraction.files_with_errors:
        print(f"  parse errors (facts may be partial): {rel}")
    print(f"repo:        {fetched.repo.url}")
    print(f"commit:      {fetched.commit_sha}")
    return store, snap


def _facts(args: argparse.Namespace) -> int:
    store, snap = _load(args)
    try:
        nodes = store.nodes(snap, args.kind)
        counts = Counter(n.kind.value for n in store.nodes(snap))
        print("facts:       " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        print(f"edges:       {len(store.edges(snap))}")
        print()
        for n in nodes:
            if n.kind == NodeKind.ENDPOINT:
                handler = n.attributes["handler"].removeprefix("java:method:")
                print(f"{n.name:<50} -> {handler}  {n.file}:{n.start_line}")
            elif n.kind == NodeKind.ENTITY:
                cls = n.attributes["class"].removeprefix("java:class:")
                print(f"{n.name:<30} <- {cls}  {n.file}:{n.start_line}")
            else:
                print(f"{n.id}  {n.file}:{n.start_line}")
    finally:
        store.close()
    return 0


_CONNECTIONS = {EdgeKind.ROUTES, EdgeKind.REQUESTS, EdgeKind.USES, EdgeKind.PUBLISHES,
                EdgeKind.CONSUMES, EdgeKind.CONNECTS}  # fmt: skip


def _structure(args: argparse.Namespace) -> int:
    store, snap = _load(args)
    try:
        result = classify(store.nodes(snap), store.edges(snap))
        services = store.nodes(snap, NodeKind.SERVICE)
        links = [e for e in store.edges(snap) if e.kind in _CONNECTIONS]
        unclear = unclear_items(store.nodes(snap), store.edges(snap))
    finally:
        store.close()
    for label in ("layout", "runtime"):
        print(f"\n{label + ':':<12} {result[label]['value']}")
        for e in result[label]["evidence"]:
            where = (
                f"  ({e['file']}{':' + str(e['line']) if e['line'] else ''})" if e["file"] else ""
            )
            print(f"  - {e['text']}{where}")
    print(f"\nservices:    {len(services)}")
    for s in services:
        a = s.attributes
        roles = ", ".join(a["roles"]) or "-"
        module = (a["module"] or "-").removeprefix("build:module:")
        print(f"  {s.name:<34} module {module:<36} port {a['port'] or '-':<6} roles {roles}")
    print(f"\nconnections: {len(links)}")
    for e in sorted(links, key=lambda e: (e.src, e.kind, e.dst)):
        src, dst = e.src.split(":", 1)[1], e.dst.split(":", 1)[1]
        print(f"  {src:<30} {e.kind:<10} {dst:<30} {e.confidence:<7} "
              f"{e.evidence_file}:{e.evidence_line}")  # fmt: skip
    print(f"\nunclear:     {len(unclear)}  (external = not analyzed, not in this repo)")
    for item in unclear:
        where = (
            f"  ({item['file']}{':' + str(item['line']) if item['line'] else ''})"
            if item["file"]
            else ""
        )
        print(f"  - [{item['kind']}] {item['text']}{where}")
    return 0


def _summarize(args: argparse.Namespace) -> int:
    try:
        settings = load_settings()
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    session = LlmSession(make_provider(settings), settings)
    with fetch_repo(args.url, args.ref) as fetched:
        extraction = analyze(fetched.path, fetched.skipped_large)
        store, _ = _store(args, fetched, extraction)
        store.close()
        cache = SummaryCache(args.db)
        # Reads class sources, so it is built while the clone still exists.
        summarizer = Summarizer(extraction, fetched.path, fetched.repo.url, fetched.commit_sha,
                                session, cache)  # fmt: skip
    try:
        return _run_summaries(args, settings, session, summarizer)
    except (LlmAuthError, LlmUnavailable, LlmError, SpendingCapExceeded, CostUnknown) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        cache.close()


def _run_summaries(args: argparse.Namespace, settings: Settings, session: LlmSession,
                   summarizer: Summarizer) -> int:  # fmt: skip
    plan = summarizer.plan()
    print(f"\nmodel:       {settings.model} (effort {settings.effort}, "
          f"{settings.concurrency} at a time)")  # fmt: skip
    view = " + 1 view spec" if args.view else ""
    print(f"to ask:      {plan.class_requests} class summaries ({plan.class_cached} cached), "
          f"then up to {plan.later_requests} module/repo summaries{view}")  # fmt: skip
    free = plan.max_usd == 0
    if free:
        print("cost:        free (the model runs on this machine); local models are slower")
    elif settings.cost_control:
        low, high = plan.min_usd or 0, plan.max_usd or 0
        print(f"cost:        at least ${low:.2f}, at most ${high:.2f} (worst case: every "
              f"answer at full length); cap ${settings.spend_cap_usd:.2f}")  # fmt: skip
    else:
        print("cost:        cost control is off: no estimate, no cap")
    if args.estimate_only:
        return 0
    ask = settings.cost_control and plan.class_requests and not free and not args.yes
    if ask and input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Stopped; nothing was spent.")
        return 1
    result = summarizer.run()
    failed = [s for level in (result.classes, result.modules) for s in level.values() if s.error]
    if result.repo is not None and result.repo.data is not None:
        print(f"\n{result.repo.data['summary']}\n\n{result.repo.data['organization']}")
    print("\nmodules:")
    for m in result.modules.values():
        print(f"  {m.name}: {m.data['summary'] if m.data else '(failed: ' + str(m.error) + ')'}")
    for s in failed:
        print(f"  ! {s.level} {s.name}: {s.error}")
    unpriced = f", {session.unpriced_calls} answers from a model without a price" \
        if session.unpriced_calls else ""  # fmt: skip
    if args.view:
        _write_view(args, summarizer, result, session)
    print(f"\nspent:       ${session.spent_usd:.4f}{unpriced}")
    return 0


def _write_view(args: argparse.Namespace, summarizer: Summarizer, result: SummaryResult,
                session: LlmSession) -> None:  # fmt: skip
    path, view = save_view(summarizer.facts, result, summarizer.repo, summarizer.sha, session,
                           summarizer.cache, args.db.parent / "views")  # fmt: skip
    kinds = Counter(s["type"] for s in view["sections"])
    print(f"\nview spec:   {path}")
    print("sections:    " + (", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) or "none"))
    print(f"removed:     {len(view['removed'])} references that matched no fact")
    for r in view["removed"][:10]:
        print(f"  - {r['where']}: {r['ref']} ({r['reason']})")


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from release_impact_brief.web.app import create_app
    from release_impact_brief.web.jobs import JobRunner

    static = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not static.is_dir():
        print("note: no built UI (frontend/dist); run `npm run build` in frontend/ first")
    print(f"release-impact-brief: http://127.0.0.1:{args.port}")
    uvicorn.run(create_app(JobRunner(args.db), static), host="127.0.0.1", port=args.port)
    return 0


def _eval(args: argparse.Namespace) -> int:
    from release_impact_brief.evaluation import run

    try:
        settings = load_settings()
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    session = LlmSession(make_provider(settings), settings)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    cache = SummaryCache(args.db)
    try:
        report = run(args.dataset, args.out, session, cache, args.only)
    except (LlmAuthError, LlmUnavailable) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        cache.close()
    print(json.dumps(report["summary"], indent=2))
    print(f"results: {args.out / 'results.json'}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rib", description="release-impact-brief")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="fetch a public GitHub repo at a pinned commit")
    fetch.add_argument("url", help="https://github.com/<owner>/<repo>")
    fetch.add_argument("--ref", help="branch, tag or full commit SHA (default: HEAD)")
    fetch.set_defaults(func=_fetch)

    facts = sub.add_parser("facts", help="extract and list code facts for a repo")
    facts.add_argument("url", help="https://github.com/<owner>/<repo>")
    facts.add_argument("--ref", help="branch, tag or full commit SHA (default: HEAD)")
    facts.add_argument(
        "--kind",
        type=NodeKind,
        default=NodeKind.CLASS,
        choices=list(NodeKind),
        help="which facts to list (default: class)",
    )
    facts.add_argument("--db", type=Path, default=Path(".rib/facts.sqlite"), help="fact store")
    facts.set_defaults(func=_facts)

    structure = sub.add_parser("structure", help="classify the repo and list its services")
    structure.add_argument("url", help="https://github.com/<owner>/<repo>")
    structure.add_argument("--ref", help="branch, tag or full commit SHA (default: HEAD)")
    structure.add_argument("--db", type=Path, default=Path(".rib/facts.sqlite"), help="fact store")
    structure.set_defaults(func=_structure)

    summarize = sub.add_parser(
        "summarize", help="summarise the repo with the LLM (class, module, repo); cached"
    )
    summarize.add_argument("url", help="https://github.com/<owner>/<repo>")
    summarize.add_argument("--ref", help="branch, tag or full commit SHA (default: HEAD)")
    summarize.add_argument("--db", type=Path, default=Path(".rib/facts.sqlite"), help="fact store")
    summarize.add_argument("--yes", action="store_true", help="don't ask before spending")
    summarize.add_argument("--estimate-only", action="store_true",
                           help="print the plan and cost estimate, then stop")  # fmt: skip
    summarize.set_defaults(func=_summarize, view=False)

    view = sub.add_parser(
        "view", help="summarise, then build the validated view spec (JSON) for the web app"
    )
    view.add_argument("url", help="https://github.com/<owner>/<repo>")
    view.add_argument("--ref", help="branch, tag or full commit SHA (default: HEAD)")
    view.add_argument("--db", type=Path, default=Path(".rib/facts.sqlite"), help="fact store")
    view.add_argument("--yes", action="store_true", help="don't ask before spending")
    view.add_argument("--estimate-only", action="store_true",
                      help="print the plan and cost estimate, then stop")  # fmt: skip
    view.set_defaults(func=_summarize, view=True)

    evaluate = sub.add_parser("eval", help="replay real bug fixes and score the impact brief")
    evaluate.add_argument("dataset", type=Path, help="e.g. eval/java/dataset.json")
    evaluate.add_argument("--out", type=Path, default=Path("eval/java"), help="results folder")
    evaluate.add_argument("--only", help="run one case id")
    evaluate.add_argument("--db", type=Path, default=Path(".rib/facts.sqlite"), help="answer cache")
    evaluate.set_defaults(func=_eval)

    serve = sub.add_parser("serve", help="run the web app on this machine (127.0.0.1 only)")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--db", type=Path, default=Path(".rib/facts.sqlite"), help="fact store")
    serve.set_defaults(func=_serve)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
