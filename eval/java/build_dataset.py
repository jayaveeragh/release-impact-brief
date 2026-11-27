"""Build eval/java/dataset.json from eval/java/cases.json.

For each case:
- the issue text, saved once (GitHub issues, Apache JIRA); a GitHub number that turns
  out to be a pull request is dropped, because a PR describes the fix and would leak it;
- the ground truth from the real fix: the changed lines on the pre-fix side, mapped to
  the methods that contain them (or the class, for changes outside any method), and the
  test classes the fix touched;
- a dev / held-out label from a hash of the case id, so nobody picks the split.
Nothing from the repos is run; both commits are fetched shallow with the locked-down
git settings every fetch uses.

    .venv/bin/python eval/java/build_dataset.py              # Java
    .venv/bin/python eval/java/build_dataset.py eval/python  # Python: same steps, *.py files
"""

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from release_impact_brief.facts import NodeKind
from release_impact_brief.fetch import _git_env, fetch_repo
from release_impact_brief.pipeline import analyze
from release_impact_brief.system import is_test

HERE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
SOURCES = "*.py" if HERE.name == "python" else "*.java"
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def group(case_id: str) -> str:
    return "held-out" if int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % 3 == 0 else "dev"


def _get(client: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
    for attempt in range(3):  # issue trackers drop connections now and then
        try:
            return client.get(url, **kwargs)
        except httpx.TransportError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def issue_text(issue: dict[str, Any], client: httpx.Client) -> str | None:
    """Title and body of the issue, or None if it's a pull request / unavailable.
    Answers are kept in issues.json, so a re-run asks the trackers nothing twice."""
    cache_file = HERE / "issues.json"
    cache = json.loads(cache_file.read_text()) if cache_file.is_file() else {}
    key = issue.get("jira") or f"{issue['github']}#{issue['number']}"
    if key not in cache:
        cache[key] = _issue_text(issue, client)
        cache_file.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")
    return cache[key]  # type: ignore[no-any-return]


def _issue_text(issue: dict[str, Any], client: httpx.Client) -> str | None:
    if "jira" in issue:
        r = _get(client, f"https://issues.apache.org/jira/rest/api/2/issue/{issue['jira']}",
                 params={"fields": "summary,description"})  # fmt: skip
        if r.status_code != 200:
            return None
        fields = r.json()["fields"]
        return f"{fields['summary']}\n\n{fields.get('description') or ''}".strip()[:4000]
    r = _get(client, f"https://api.github.com/repos/{issue['github']}/issues/{issue['number']}")
    if r.status_code != 200 or "pull_request" in r.json():
        return None
    data = r.json()
    return f"{data['title']}\n\n{data.get('body') or ''}".strip()[:4000]


def changed_lines(repo: str, pre: str, fix: str) -> dict[str, list[tuple[int, int]]]:
    """Pre-fix side of the fix's source diff: file -> [(first line, count)]; count 0 = insertion."""
    with tempfile.TemporaryDirectory(prefix="rib-diff-") as tmp:

        def git(*args: str) -> str:
            # no background gc/maintenance: it would still be writing while the folder goes
            return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "gc.auto=0",
                                   "-c", "maintenance.auto=false", *args], cwd=tmp,
                                  env=_git_env(), check=True, capture_output=True, text=True,
                                  timeout=600).stdout  # fmt: skip

        git("init", "--quiet")
        for sha in (pre, fix):
            git("fetch", "--quiet", "--depth", "1", f"https://github.com/{repo}.git", sha)
        diff = git("diff", "-U0", pre, fix, "--", SOURCES)
    hunks: dict[str, list[tuple[int, int]]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("--- "):
            current = line[6:] if line.startswith("--- a/") else None
        elif (m := _HUNK.match(line)) and current:
            hunks.setdefault(current, []).append((int(m.group(1)), int(m.group(2) or "1")))
    return hunks


def ground_truth(case: dict[str, Any]) -> dict[str, Any]:
    hunks = changed_lines(case["repo"], case["pre_fix"], case["fix"])
    with fetch_repo(f"https://github.com/{case['repo']}", case["pre_fix"]) as fetched:
        facts = analyze(fetched.path, fetched.skipped_large)
    methods = [n for n in facts.nodes if n.kind == NodeKind.METHOD]
    classes = [n for n in facts.nodes if n.kind == NodeKind.CLASS]
    changed_methods, changed_classes, changed_tests = set(), set(), set()
    for file, ranges in hunks.items():
        if is_test(file):
            changed_tests.add(Path(file).stem)
            continue
        for start, count in ranges:
            lo, hi = (start, start + count - 1) if count else (start, start)
            hit = [m for m in methods if m.file == file and m.start_line <= hi and lo <= m.end_line]
            if hit:
                changed_methods |= {m.id for m in hit}
            else:  # a field, import or new method: the innermost class holding the line
                around = [c for c in classes if c.file == file and c.start_line <= hi
                          and lo <= c.end_line]  # fmt: skip
                if around:
                    changed_classes.add(min(around, key=lambda c: c.end_line - c.start_line).id)
    main_methods = sum(1 for m in methods if not is_test(m.file))
    return {"changed_methods": sorted(changed_methods), "changed_classes": sorted(changed_classes),
            "changed_tests": sorted(changed_tests), "main_methods": main_methods}  # fmt: skip


def main() -> None:
    cases = json.loads((HERE / "cases.json").read_text())["cases"]
    kept, dropped = [], []
    # renamed repos (tiangolo/fastapi -> fastapi/fastapi) answer with a redirect
    with httpx.Client(
        timeout=30, follow_redirects=True, headers={"User-Agent": "release-impact-brief-eval"}
    ) as client:
        for case in cases:
            text = issue_text(case["issue"], client)
            if text is None:
                dropped.append({"id": case["id"], "reason": "pull request or issue not found"})
                print(f"drop {case['id']}: pull request or issue not found", flush=True)
                continue
            truth = ground_truth(case)
            if not truth["changed_methods"] and not truth["changed_classes"]:
                dropped.append({"id": case["id"], "reason": "the fix changes no main code"})
                print(f"drop {case['id']}: no main code change", flush=True)
                continue
            kept.append(case | {"issue_text": text, "group": group(case["id"])} | truth)
            print(f"keep {case['id']} ({kept[-1]['group']}): {len(truth['changed_methods'])} "
                  f"methods, {len(truth['changed_classes'])} classes, "
                  f"{len(truth['changed_tests'])} tests", flush=True)  # fmt: skip
    (HERE / "dataset.json").write_text(json.dumps(
        {"built": f"by eval/java/build_dataset.py from eval/{HERE.name}/cases.json", "cases": kept,
         "dropped": dropped}, indent=2) + "\n")  # fmt: skip
    print(f"{len(kept)} cases kept, {len(dropped)} dropped", file=sys.stderr)


if __name__ == "__main__":
    main()
