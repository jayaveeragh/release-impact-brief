import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from release_impact_brief.facts import Confidence, Edge, EdgeKind, FactStore, Node, NodeKind

SHA_A = "a" * 40
SHA_B = "b" * 40
REPO = "github.com/example/demo"


@pytest.fixture
def store() -> Iterator[FactStore]:
    s = FactStore(":memory:")
    yield s
    s.close()


def method(node_id: str, line: int = 10) -> Node:
    return Node(
        id=node_id,
        kind=NodeKind.METHOD,
        name=node_id,
        file="src/Demo.java",
        start_line=line,
        end_line=line + 5,
        language="java",
    )


def call(edge_id: str, src: str, dst: str) -> Edge:
    return Edge(
        id=edge_id,
        src=src,
        dst=dst,
        kind=EdgeKind.CALLS,
        confidence=Confidence.EXACT,
        evidence_file="src/Demo.java",
        evidence_line=12,
        resolver="test",
    )


def test_node_round_trip_keeps_attributes(store: FactStore) -> None:
    snap = store.snapshot(REPO, SHA_A)
    node = Node(
        id="ep-1",
        kind=NodeKind.ENDPOINT,
        name="GET /owners",
        file="src/OwnerController.java",
        start_line=40,
        end_line=48,
        language="java",
        attributes={"http_method": "GET", "path": "/owners"},
    )
    store.add_nodes(snap, [node])
    assert store.get_node(snap, "ep-1") == node


def test_edge_round_trip(store: FactStore) -> None:
    snap = store.snapshot(REPO, SHA_A)
    store.add_nodes(snap, [method("m-1"), method("m-2", 30)])
    edge = call("e-1", "m-1", "m-2")
    store.add_edges(snap, [edge])
    assert store.edges(snap) == [edge]


def test_nodes_filter_by_kind(store: FactStore) -> None:
    snap = store.snapshot(REPO, SHA_A)
    cls = Node("c-1", NodeKind.CLASS, "Demo", "src/Demo.java", 1, 50, "java")
    store.add_nodes(snap, [cls, method("m-1")])
    assert store.nodes(snap, NodeKind.CLASS) == [cls]
    assert len(store.nodes(snap)) == 2


def test_snapshot_is_reused_for_same_repo_and_sha(store: FactStore) -> None:
    assert store.snapshot(REPO, SHA_A) == store.snapshot(REPO, SHA_A)


def test_snapshots_for_different_shas_are_isolated(store: FactStore) -> None:
    snap_a = store.snapshot(REPO, SHA_A)
    snap_b = store.snapshot(REPO, SHA_B)
    store.add_nodes(snap_a, [method("m-1")])
    assert snap_a != snap_b
    assert store.nodes(snap_b) == []
    assert store.get_node(snap_b, "m-1") is None


@pytest.mark.parametrize("sha", ["abc123", "A" * 40, "g" * 40, ""])
def test_rejects_non_full_sha(store: FactStore, sha: str) -> None:
    with pytest.raises(ValueError):
        store.snapshot(REPO, sha)


def test_rejects_unknown_confidence_label() -> None:
    with pytest.raises(ValueError):
        Edge("e-1", "m-1", "m-2", "calls", "probably", "f", 1, "test")  # type: ignore[arg-type]


def test_rejects_unknown_edge_kind() -> None:
    with pytest.raises(ValueError):
        Edge("e-1", "m-1", "m-2", "knows", Confidence.EXACT, "f", 1, "test")  # type: ignore[arg-type]


def test_rejects_unknown_node_kind() -> None:
    with pytest.raises(ValueError):
        Node("x-1", "widget", "x", "f", 1, 1, "java")  # type: ignore[arg-type]


@pytest.mark.parametrize(("start", "end"), [(0, 1), (5, 4)])
def test_rejects_invalid_line_range(start: int, end: int) -> None:
    with pytest.raises(ValueError):
        Node("m-1", NodeKind.METHOD, "m", "f", start, end, "java")


def test_edge_to_missing_node_is_rejected(store: FactStore) -> None:
    snap = store.snapshot(REPO, SHA_A)
    store.add_nodes(snap, [method("m-1")])
    with pytest.raises(sqlite3.IntegrityError):
        store.add_edges(snap, [call("e-1", "m-1", "does-not-exist")])


def test_edge_cannot_point_into_another_snapshot(store: FactStore) -> None:
    snap_a = store.snapshot(REPO, SHA_A)
    snap_b = store.snapshot(REPO, SHA_B)
    store.add_nodes(snap_a, [method("m-1"), method("m-2", 30)])
    store.add_nodes(snap_b, [method("m-1")])
    with pytest.raises(sqlite3.IntegrityError):
        store.add_edges(snap_b, [call("e-1", "m-1", "m-2")])


def test_database_rejects_bad_confidence_even_without_python_checks(store: FactStore) -> None:
    snap = store.snapshot(REPO, SHA_A)
    store.add_nodes(snap, [method("m-1"), method("m-2", 30)])
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO edges VALUES (?, 'e-1', 'm-1', 'm-2', 'calls', '90%', 'f', 1, 'raw')",
            (snap,),
        )


def test_database_rejects_bad_edge_kind_even_without_python_checks(store: FactStore) -> None:
    snap = store.snapshot(REPO, SHA_A)
    store.add_nodes(snap, [method("m-1"), method("m-2", 30)])
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO edges VALUES (?, 'e-1', 'm-1', 'm-2', 'knows', 'exact', 'f', 1, 'raw')",
            (snap,),
        )


def test_replace_facts_swaps_a_snapshot_without_touching_others(store: FactStore) -> None:
    snap_a = store.snapshot(REPO, SHA_A)
    snap_b = store.snapshot(REPO, SHA_B)
    store.add_facts(snap_a, [method("m-1"), method("m-2", 30)], [call("e-1", "m-1", "m-2")])
    store.add_facts(snap_b, [method("m-1")], [])
    store.replace_facts(snap_a, [method("m-3")], [])
    assert [n.id for n in store.nodes(snap_a)] == ["m-3"]
    assert store.edges(snap_a) == []
    assert [n.id for n in store.nodes(snap_b)] == ["m-1"]


def test_database_with_an_older_schema_is_rebuilt(tmp_path: Path) -> None:
    db = tmp_path / "facts.sqlite"
    old = sqlite3.connect(db)
    old.executescript(
        "CREATE TABLE snapshots (id INTEGER PRIMARY KEY, repo TEXT, commit_sha TEXT);"
        "CREATE TABLE nodes (snapshot_id INTEGER, id TEXT);"
        "CREATE TABLE edges (snapshot_id INTEGER, id TEXT, kind TEXT CHECK (kind IN ('calls')));"
    )
    old.close()
    store = FactStore(db)
    try:
        snap = store.snapshot(REPO, SHA_A)
        store.add_nodes(snap, [method("m-1"), method("m-2", 30)])
        edge = Edge("e-1", "m-1", "m-2", EdgeKind.USES, Confidence.EXACT, "f", 1, "test")
        store.add_edges(snap, [edge])
        assert store.edges(snap) == [edge]
    finally:
        store.close()
    assert FactStore(db).nodes(snap) != []  # same schema: kept, not rebuilt again
