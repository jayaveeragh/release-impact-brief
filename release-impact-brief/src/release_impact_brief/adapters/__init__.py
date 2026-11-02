"""Language adapters turn source files into facts.

Nothing above this package should know which language it is handling.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from release_impact_brief.facts import Edge, Node


@dataclass
class Extraction:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    files_parsed: int = 0
    files_with_errors: list[str] = field(default_factory=list)


class LanguageAdapter(Protocol):
    language: str
    extensions: tuple[str, ...]

    def extract(self, root: Path, files: Sequence[str]) -> Extraction:
        """Extract facts from files (posix paths relative to root)."""
        ...


def extract_all(
    root: Path, adapters: Iterable[LanguageAdapter], skip: Iterable[str] = ()
) -> Extraction:
    """Run each adapter over its files under root, skipping .git and the given paths."""
    skipped = set(skip)
    result = Extraction()
    for adapter in adapters:
        files = sorted(
            rel
            for path in root.rglob("*")
            if path.suffix in adapter.extensions
            and path.is_file()
            and ".git" not in path.relative_to(root).parts
            and (rel := path.relative_to(root).as_posix()) not in skipped
        )
        part = adapter.extract(root, files)
        result.nodes += part.nodes
        result.edges += part.edges
        result.files_parsed += part.files_parsed
        result.files_with_errors += part.files_with_errors
    return result
