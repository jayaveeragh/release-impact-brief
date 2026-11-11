"""Fact store: deterministic code facts, keyed by repo and commit SHA.

Every fact carries a file, a line and a confidence label so that views built
on top can always point back to evidence.
"""

import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class Confidence(StrEnum):
    EXACT = "exact"
    LIKELY = "likely"
    GUESS = "guess"
    UNKNOWN = "unknown"


class NodeKind(StrEnum):
    MODULE = "module"
    SERVICE = "service"
    CLASS = "class"
    METHOD = "method"
    ENDPOINT = "endpoint"
    ENTITY = "entity"
    FIELD = "field"
    DATA_STORE = "data_store"
    EXTERNAL_SYSTEM = "external_system"
    CONFIG = "config"
    TEST = "test"


class EdgeKind(StrEnum):
    CONTAINS = "contains"
    CALLS = "calls"
    EXTENDS = "extends"
    IMPLEMENTS = "implements"
    OVERRIDES = "overrides"
    INJECTS = "injects"
    HANDLES = "handles"  # endpoint -> handler method
    MAPS = "maps"  # class -> JPA entity
    RELATES = "relates"  # relation field -> target entity
    STORES = "stores"  # repository -> entity
    DEPENDS_ON = "depends_on"  # module -> module, deployed service -> service


_SHA_RE = re.compile(r"[0-9a-f]{40}")


def _check_lines(start: int, end: int) -> None:
    if start < 1 or end < start:
        raise ValueError(f"invalid line range {start}-{end}")


@dataclass(frozen=True)
class Node:
    id: str
    kind: NodeKind
    name: str
    file: str
    start_line: int
    end_line: int
    language: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", NodeKind(self.kind))
        if not self.id:
            raise ValueError("node id is required")
        _check_lines(self.start_line, self.end_line)


@dataclass(frozen=True)
class Edge:
    id: str
    src: str
    dst: str
    kind: EdgeKind
    confidence: Confidence
    evidence_file: str
    evidence_line: int
    resolver: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", EdgeKind(self.kind))
        object.__setattr__(self, "confidence", Confidence(self.confidence))
        if not self.id:
            raise ValueError("edge id is required")
        _check_lines(self.evidence_line, self.evidence_line)


def _in_list(values: Iterable[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    UNIQUE (repo, commit_sha)
);
CREATE TABLE IF NOT EXISTS nodes (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ({_in_list(NodeKind)})),
    name TEXT NOT NULL,
    file TEXT NOT NULL,
    start_line INTEGER NOT NULL CHECK (start_line >= 1),
    end_line INTEGER NOT NULL CHECK (end_line >= start_line),
    language TEXT NOT NULL,
    attributes TEXT NOT NULL DEFAULT '{{}}',
    PRIMARY KEY (snapshot_id, id)
);
CREATE TABLE IF NOT EXISTS edges (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ({_in_list(EdgeKind)})),
    confidence TEXT NOT NULL CHECK (confidence IN ({_in_list(Confidence)})),
    evidence_file TEXT NOT NULL,
    evidence_line INTEGER NOT NULL CHECK (evidence_line >= 1),
    resolver TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, id),
    FOREIGN KEY (snapshot_id, src) REFERENCES nodes(snapshot_id, id),
    FOREIGN KEY (snapshot_id, dst) REFERENCES nodes(snapshot_id, id)
);
"""


class FactStore:
    def __init__(self, path: Path | str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def snapshot(self, repo: str, commit_sha: str) -> int:
        """Return the snapshot id for repo@sha, creating it if needed."""
        if not _SHA_RE.fullmatch(commit_sha):
            raise ValueError(f"not a full lowercase commit SHA: {commit_sha!r}")
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO snapshots (repo, commit_sha) VALUES (?, ?)",
                (repo, commit_sha),
            )
        row = self._conn.execute(
            "SELECT id FROM snapshots WHERE repo = ? AND commit_sha = ?", (repo, commit_sha)
        ).fetchone()
        return int(row[0])

    def add_facts(self, snapshot_id: int, nodes: Iterable[Node], edges: Iterable[Edge]) -> None:
        """Insert nodes and edges in one transaction, so a snapshot is never half-written."""
        with self._conn:
            self._insert_nodes(snapshot_id, nodes)
            self._insert_edges(snapshot_id, edges)

    def replace_facts(self, snapshot_id: int, nodes: Iterable[Node], edges: Iterable[Edge]) -> None:
        """Swap a snapshot's facts for new ones in one transaction (re-extraction)."""
        with self._conn:
            self._conn.execute("DELETE FROM edges WHERE snapshot_id = ?", (snapshot_id,))
            self._conn.execute("DELETE FROM nodes WHERE snapshot_id = ?", (snapshot_id,))
            self._insert_nodes(snapshot_id, nodes)
            self._insert_edges(snapshot_id, edges)

    def add_nodes(self, snapshot_id: int, nodes: Iterable[Node]) -> None:
        with self._conn:
            self._insert_nodes(snapshot_id, nodes)

    def add_edges(self, snapshot_id: int, edges: Iterable[Edge]) -> None:
        with self._conn:
            self._insert_edges(snapshot_id, edges)

    def _insert_nodes(self, snapshot_id: int, nodes: Iterable[Node]) -> None:
        self._conn.executemany(
            "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    snapshot_id,
                    n.id,
                    n.kind.value,
                    n.name,
                    n.file,
                    n.start_line,
                    n.end_line,
                    n.language,
                    json.dumps(n.attributes, sort_keys=True),
                )
                for n in nodes
            ],
        )

    def _insert_edges(self, snapshot_id: int, edges: Iterable[Edge]) -> None:
        self._conn.executemany(
            "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    snapshot_id,
                    e.id,
                    e.src,
                    e.dst,
                    e.kind.value,
                    e.confidence.value,
                    e.evidence_file,
                    e.evidence_line,
                    e.resolver,
                )
                for e in edges
            ],
        )

    def get_node(self, snapshot_id: int, node_id: str) -> Node | None:
        row = self._conn.execute(
            "SELECT id, kind, name, file, start_line, end_line, language, attributes"
            " FROM nodes WHERE snapshot_id = ? AND id = ?",
            (snapshot_id, node_id),
        ).fetchone()
        return _node(row) if row else None

    def nodes(self, snapshot_id: int, kind: NodeKind | None = None) -> list[Node]:
        sql = (
            "SELECT id, kind, name, file, start_line, end_line, language, attributes"
            " FROM nodes WHERE snapshot_id = ?"
        )
        params: tuple[Any, ...] = (snapshot_id,)
        if kind is not None:
            sql += " AND kind = ?"
            params += (NodeKind(kind).value,)
        return [_node(r) for r in self._conn.execute(sql + " ORDER BY id", params)]

    def edges(self, snapshot_id: int) -> list[Edge]:
        rows = self._conn.execute(
            "SELECT id, src, dst, kind, confidence, evidence_file, evidence_line, resolver"
            " FROM edges WHERE snapshot_id = ? ORDER BY id",
            (snapshot_id,),
        )
        return [Edge(*r) for r in rows]


def _node(row: tuple[Any, ...]) -> Node:
    node_id, kind, name, file, start_line, end_line, language, attributes = row
    return Node(node_id, kind, name, file, start_line, end_line, language, json.loads(attributes))
