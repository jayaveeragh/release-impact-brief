"""Replay real bug fixes and measure the impact brief against what the fix changed.

For each case of a dataset (eval/java/dataset.json): analyse the pre-fix commit, give
the tool only the issue text, let the model pick the locations from its shortlist (no
human; if it picks nothing, the top 3 of the shortlist are used and that is recorded),
build the brief, and compare with the ground truth from the real fix:
- hit: the high set (locations + high items) contains a method the fix changed (a fix
  that changes no method: a method of a class it changed);
  class hit: it contains a method of a class the fix changed;
- located: the model's picks include a changed method (or one of its classes);
- coverage: methods in the high set / main-code methods (smaller is more useful);
- test recall: tests the fix touched that the brief lists, if the fix touched any.
No summaries are made (faster; and the harder setting for location picking).
"""

import json
import time
from pathlib import Path
from typing import Any

from release_impact_brief.facts import NodeKind
from release_impact_brief.fetch import fetch_repo
from release_impact_brief.impact import analyze_impact, bug_text, pick_candidates
from release_impact_brief.llm import LlmSession
from release_impact_brief.pipeline import analyze
from release_impact_brief.summaries import SummaryCache
from release_impact_brief.system import is_test


def _class_of(method_id: str) -> str:
    return method_id.replace(":method:", ":class:", 1).split("#", 1)[0]


def run_case(case: dict[str, Any], session: LlmSession, cache: SummaryCache) -> dict[str, Any]:
    started = time.monotonic()
    with fetch_repo(f"https://github.com/{case['repo']}", case["pre_fix"]) as fetched:
        facts = analyze(fetched.path, fetched.skipped_large)
    view = {"repo": f"https://github.com/{case['repo']}", "commit": case["pre_fix"],
            "panels": {"modules": []}, "sections": []}  # fmt: skip
    picked = pick_candidates(facts, view, bug_text(case["issue_text"]), session, cache)
    locations = [c["id"] for c in picked["picked"]]
    fallback = not locations
    if fallback:
        locations = [c["id"] for c in picked["shortlist"][:3]]
    brief = analyze_impact(facts, view, locations)

    nodes = {n.id: n for n in facts.nodes}
    high_methods = set()
    for loc in locations:
        n = nodes[loc]
        if n.kind == NodeKind.METHOD:
            high_methods.add(loc)
        elif n.kind == NodeKind.ENDPOINT:
            high_methods.add(n.attributes["handler"])
    for item in brief["high"]:
        found = nodes.get(item["id"])
        if found is not None and found.kind == NodeKind.METHOD:
            high_methods.add(found.id)
        elif found is not None and found.kind == NodeKind.ENDPOINT:
            high_methods.add(found.attributes["handler"])
    changed = set(case["changed_methods"])
    changed_classes = set(case["changed_classes"]) | {_class_of(m) for m in changed}
    high_classes = {_class_of(m) for m in high_methods}
    picked_methods = {loc for loc in locations if nodes[loc].kind == NodeKind.METHOD}
    picked_classes = {_class_of(m) for m in picked_methods} | {
        loc for loc in locations if nodes[loc].kind == NodeKind.CLASS
    }
    listed_tests = {t["label"] for t in brief["tests"]}
    changed_tests = set(case["changed_tests"])
    main_methods = sum(1 for n in facts.nodes if n.kind == NodeKind.METHOD and not is_test(n.file))
    return {
        "id": case["id"], "source": case["source"], "group": case["group"],
        # a fix that touches no method (a field, a new method) can only be met at class level
        "hit": bool(changed & high_methods) if changed else bool(changed_classes & high_classes),
        "class_hit": bool(changed_classes & high_classes),
        "located": bool(changed & picked_methods)
        if changed else bool(changed_classes & picked_classes),
        "located_class": bool(changed_classes & picked_classes),
        "fallback_locations": fallback,
        "high_methods": len(high_methods), "main_methods": main_methods,
        "coverage": round(len(high_methods) / main_methods, 4) if main_methods else None,
        "tests_changed": sorted(changed_tests),
        "tests_listed_of_changed": sorted(changed_tests & listed_tests),
        "test_recall": round(len(changed_tests & listed_tests) / len(changed_tests), 3)
        if changed_tests else None,
        "size": brief["size"]["value"],
        "locations": [loc.split(":", 2)[-1] for loc in locations],
        "changed_methods": [m.split(":", 2)[-1] for m in sorted(changed)],
        "seconds": round(time.monotonic() - started, 1),
    }  # fmt: skip


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    def rate(rows: list[dict[str, Any]], key: str) -> str:
        return f"{sum(1 for r in rows if r[key])}/{len(rows)}" if rows else "-"

    def mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    out: dict[str, Any] = {}
    groups = {"all": results}
    for key in ("group", "source"):
        for value in sorted({r[key] for r in results}):
            groups[f"{key}={value}"] = [r for r in results if r[key] == value]
    for name, rows in groups.items():
        with_tests = [r for r in rows if r["test_recall"] is not None]
        out[name] = {
            "cases": len(rows), "hit": rate(rows, "hit"), "class_hit": rate(rows, "class_hit"),
            "located": rate(rows, "located"), "located_class": rate(rows, "located_class"),
            "mean_coverage": mean([r["coverage"] for r in rows if r["coverage"] is not None]),
            "mean_test_recall": mean([r["test_recall"] for r in with_tests]),
            "cases_with_changed_tests": len(with_tests),
        }  # fmt: skip
    return out


def run(dataset: Path, out_dir: Path, session: LlmSession, cache: SummaryCache,
        only: str | None = None) -> dict[str, Any]:  # fmt: skip
    cases = json.loads(dataset.read_text())["cases"]
    results, failures = [], []
    for case in cases:
        if only and case["id"] != only:
            continue
        try:
            result = run_case(case, session, cache)
        except Exception as error:  # one broken case shouldn't hide the others
            failures.append({"id": case["id"], "error": f"{type(error).__name__}: {error}"})
            print(f"FAIL {case['id']}: {error}", flush=True)
            continue
        results.append(result)
        mark = "HIT " if result["hit"] else "miss"
        print(f"{mark} {result['id']:<34} located={result['located']}"
              f" coverage={result['coverage']} tests={result['test_recall']}"
              f" ({result['seconds']} s)", flush=True)  # fmt: skip
    report = {"model": session.provider.model, "summary": summarize(results),
              "cases": results, "failures": failures}  # fmt: skip
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
