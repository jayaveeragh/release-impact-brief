"""Command-line entry point: `rib`."""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from release_impact_brief.adapters import extract_all
from release_impact_brief.adapters.java import JavaAdapter
from release_impact_brief.facts import FactStore, NodeKind
from release_impact_brief.fetch import FetchError, Limits, fetch_repo, parse_github_url, resolve_sha


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


def _facts(args: argparse.Namespace) -> int:
    repo = parse_github_url(args.url)
    sha = resolve_sha(repo.url, args.ref, Limits())
    args.db.parent.mkdir(parents=True, exist_ok=True)
    store = FactStore(args.db)
    try:
        snap = store.snapshot(repo.url, sha)
        cached = bool(store.nodes(snap))
        if not cached:
            with fetch_repo(repo.url, sha) as fetched:
                extraction = extract_all(fetched.path, [JavaAdapter()], fetched.skipped_large)
            store.add_facts(snap, extraction.nodes, extraction.edges)
            print(f"parsed:      {extraction.files_parsed} files")
            for rel in extraction.files_with_errors:
                print(f"  syntax errors (facts may be partial): {rel}")
        nodes = store.nodes(snap, args.kind)
        print(f"repo:        {repo.url}")
        print(f"commit:      {sha}{'  (cached)' if cached else ''}")
        counts = Counter(n.kind.value for n in store.nodes(snap))
        print("facts:       " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        print(f"edges:       {len(store.edges(snap))}")
        print()
        for n in nodes:
            if n.kind == NodeKind.ENDPOINT:
                handler = n.attributes["handler"].removeprefix("java:method:")
                print(f"{n.name:<50} -> {handler}  {n.file}:{n.start_line}")
            else:
                print(f"{n.id}  {n.file}:{n.start_line}")
    finally:
        store.close()
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

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
