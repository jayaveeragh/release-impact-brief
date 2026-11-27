"""List candidate bug-fix commits in public Spring apps, for hand-picking the dataset.

Reads commit history only: a partial clone without file contents (`--filter=blob:none`,
`--no-checkout`), made with the same locked-down git settings as every fetch. Nothing
from the repos is checked out or run. A candidate is a non-merge commit whose message
mentions a fix and an issue (`#123`, `fixes`, `gh-123`) and which changes main Java code
in at most 5 files.

    .venv/bin/python eval/java/find_candidates.py <out.json> owner/repo ...
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from release_impact_brief.fetch import _git_env

_FIX = re.compile(r"\b(fix(e[sd])?|bug|closes?|resolves?)\b", re.I)
_ISSUE = re.compile(r"(?:#|gh-)(\d+)")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd,
                          env=_git_env(), check=True, capture_output=True, text=True,
                          timeout=600).stdout  # fmt: skip


def candidates(repo: str, max_files: int = 5) -> list[dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix="rib-hist-") as tmp:
        git(Path(tmp), "clone", "--quiet", "--filter=blob:none", "--no-checkout", "--bare",
            f"https://github.com/{repo}.git", "h")  # fmt: skip
        where = Path(tmp, "h")
        log = git(where, "log", "--no-merges", "--format=%x00%H %P%n%s%n%b", "--name-only")
    found = []
    for entry in log.split("\x00")[1:]:
        head, _, rest = entry.partition("\n")
        sha, *parents = head.split()
        lines = rest.strip().splitlines()
        subject = lines[0] if lines else ""
        files = [line for line in lines[1:] if line.endswith(".java")]
        message = "\n".join(lines[: len(lines) - len(files)])
        main = [f for f in files if "/src/main/" in f"/{f}"]
        issues = _ISSUE.findall(message)
        if (
            len(parents) == 1
            and _FIX.search(message)
            and issues
            and main
            and len(main) <= max_files
        ):
            found.append({"repo": repo, "fix": sha, "pre_fix": parents[0], "subject": subject,
                          "issues": sorted(set(issues)), "main_files": main,
                          "test_files": [f for f in files if "/src/test/" in f"/{f}"]})  # fmt: skip
    return found


if __name__ == "__main__":
    out = Path(sys.argv[1])
    found = [c for repo in sys.argv[2:] for c in candidates(repo)]
    out.write_text(json.dumps(found, indent=2) + "\n")
    print(f"{len(found)} candidates -> {out}")
