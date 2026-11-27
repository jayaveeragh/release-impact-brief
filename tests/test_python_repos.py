"""Python adapter against hand-written answer keys for two widely used public repos."""

import json
from pathlib import Path

import pytest

from release_impact_brief.branches import branch_map
from release_impact_brief.fetch import fetch_repo
from release_impact_brief.pipeline import analyze

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.network
@pytest.mark.parametrize("name", ["full-stack-fastapi-template", "microblog"])
def test_python_repo_matches_hand_written_answer_key(name: str) -> None:
    key = json.loads((FIXTURES / name / "expected.json").read_text())
    with fetch_repo(key["repo"], key["commit_sha"]) as fetched:
        facts = analyze(fetched.path)
    assert not facts.files_with_errors
    endpoints = {n.name: n for n in facts.nodes if n.kind == "endpoint"}
    assert sorted(endpoints) == key["endpoints"]
    handles = {e.src: e.confidence.value for e in facts.edges if e.kind == "handles"}
    assert {handles[n.id] for n in endpoints.values()} == {key["endpoint_confidence"]}
    entities = {n.id.removeprefix("python:entity:"): n.name for n in facts.nodes
                if n.kind == "entity"}  # fmt: skip
    assert entities == key["entities"]
    relations = sorted([e.src.removeprefix("python:field:"), e.dst.removeprefix("python:entity:")]
                       for e in facts.edges if e.kind == "relates")  # fmt: skip
    assert relations == sorted(key["relations"])
    services = [[n.name, n.attributes["module"]] for n in facts.nodes if n.kind == "service"]
    assert services == key["services"]
    assert sorted(n.name for n in facts.nodes if n.kind == "data_store") == key["stores"]

    want = key["branches"]
    bm = branch_map(facts.nodes, facts.edges, f"python:endpoint:{want['endpoint']}")
    assert {str(b["line"]): b["drivers"] for b in bm["branches"]} == want["lines"]
    roles = {i["name"]: i["role"] for i in bm["inputs"]}
    assert {k: roles.get(k) for k in want["inputs"]} == want["inputs"]
