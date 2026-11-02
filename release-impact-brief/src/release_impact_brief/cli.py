"""Command-line entry point: `rib`."""

import argparse
import sys
from collections.abc import Sequence

from release_impact_brief.fetch import FetchError, fetch_repo


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rib", description="release-impact-brief")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="fetch a public GitHub repo at a pinned commit")
    fetch.add_argument("url", help="https://github.com/<owner>/<repo>")
    fetch.add_argument("--ref", help="branch, tag or full commit SHA (default: HEAD)")
    fetch.set_defaults(func=_fetch)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
