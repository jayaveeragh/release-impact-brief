"""Command-line entry point: `rib`."""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from release_impact_brief.facts import EdgeKind, FactStore, NodeKind
from release_impact_brief.fetch import FetchError, fetch_repo
from release_impact_brief.pipeline import analyze
from release_impact_brief.system import classify


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
                EdgeKind.CONSUMES}  # fmt: skip


def _structure(args: argparse.Namespace) -> int:
    store, snap = _load(args)
    try:
        result = classify(store.nodes(snap), store.edges(snap))
        services = store.nodes(snap, NodeKind.SERVICE)
        links = [e for e in store.edges(snap) if e.kind in _CONNECTIONS]
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
    for s in services:
        for u in s.attributes.get("unresolved_targets", []):
            print(f"  {s.name:<30} {'?':<10} {u['target']:<30} unknown {u['file']}:{u['line']}")
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

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
