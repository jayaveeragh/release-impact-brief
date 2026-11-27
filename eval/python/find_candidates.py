"""List BugsInPy bugs whose fix commit message names an issue, for hand-picking the dataset.

Reads only data: each bug's `bug.info` (the buggy and fixed commits) from the BugsInPy
repo, and the fix commit's message from a history-only clone of the project
(`--filter=blob:none`, bare), made with the same locked-down git settings as every fetch.
Nothing from BugsInPy or the projects is checked out or run. The `#123` numbers found
may be pull requests; build_dataset.py drops those (a PR describes the fix).

    .venv/bin/python eval/python/find_candidates.py <out.json> black fastapi ...
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from release_impact_brief.fetch import _git_env

_RAW = "https://raw.githubusercontent.com/soarsmu/BugsInPy/master/projects"
_ISSUE = re.compile(r"(?:#|gh-|issues/)(\d+)")
_INFO = re.compile(r'^(\w+)="?([^"\n]*)"?', re.M)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd,
                          env=_git_env(), check=True, capture_output=True, text=True,
                          timeout=900).stdout  # fmt: skip


def info(client: httpx.Client, path: str) -> dict[str, str] | None:
    r = client.get(f"{_RAW}/{path}")
    return dict(_INFO.findall(r.text)) if r.status_code == 200 else None


def candidates(project: str, client: httpx.Client) -> list[dict[str, object]]:
    meta = info(client, f"{project}/project.info")
    if meta is None:
        raise SystemExit(f"{project}: no project.info in BugsInPy")
    repo = meta["github_url"].removeprefix("https://github.com/").rstrip("/")
    bugs = []
    for number in range(1, 200):
        bug = info(client, f"{project}/bugs/{number}/bug.info")
        if bug is None:
            break
        if "fixed_commit_id" in bug and "buggy_commit_id" in bug:  # a few files are malformed
            bugs.append((number, bug))
    found = []
    with tempfile.TemporaryDirectory(prefix="rib-hist-") as tmp:
        git(Path(tmp), "clone", "--quiet", "--filter=blob:none", "--no-checkout", "--bare",
            f"https://github.com/{repo}.git", "h")  # fmt: skip
        where = Path(tmp, "h")
        for number, bug in bugs:
            fix = bug["fixed_commit_id"]
            try:
                message = git(where, "show", "-s", "--format=%B", fix)
                files = git(where, "diff", "--name-only", bug["buggy_commit_id"], fix).split()
            except subprocess.CalledProcessError:
                print(f"skip {project}-{number}: commit not in history", file=sys.stderr)
                continue
            issues = _ISSUE.findall(message)
            if issues:
                found.append({"id": f"{project}-{number}", "repo": repo,
                              "pre_fix": bug["buggy_commit_id"], "fix": fix,
                              "subject": message.strip().splitlines()[0],
                              "issues": sorted(set(issues), key=int),
                              "py_files": [f for f in files if f.endswith(".py")]})  # fmt: skip
    return found


if __name__ == "__main__":
    out = Path(sys.argv[1])
    with httpx.Client(timeout=30, headers={"User-Agent": "release-impact-brief-eval"}) as client:
        found = []
        for project in sys.argv[2:]:
            rows = candidates(project, client)
            print(f"{project}: {len(rows)} with an issue number", flush=True)
            found += rows
    out.write_text(json.dumps(found, indent=2) + "\n")
    print(f"{len(found)} candidates -> {out}")
