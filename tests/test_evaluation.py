from typing import Any

from release_impact_brief.evaluation import summarize


def row(case_id: str, group: str, source: str, hit: bool, recall: float | None) -> dict[str, Any]:
    return {"id": case_id, "group": group, "source": source, "hit": hit, "class_hit": hit,
            "located": False, "located_class": hit, "coverage": 0.1,
            "test_recall": recall}  # fmt: skip


def test_summary_keeps_held_out_apart_and_skips_cases_without_tests() -> None:
    summary = summarize([row("a", "dev", "d4j", True, 1.0), row("b", "dev", "d4j", False, None),
                         row("c", "held-out", "spring-app", True, 0.0)])  # fmt: skip
    assert summary["all"]["hit"] == "2/3"
    assert summary["group=dev"]["hit"] == "1/2"
    assert summary["group=held-out"]["hit"] == "1/1"
    assert summary["source=d4j"]["mean_test_recall"] == 1.0  # case b touched no tests
    assert summary["all"]["cases_with_changed_tests"] == 2
