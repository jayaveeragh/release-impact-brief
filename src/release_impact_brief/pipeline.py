"""The full extraction pipeline for one checked-out repo."""

from collections.abc import Iterable
from pathlib import Path

from release_impact_brief.adapters import Extraction, LanguageAdapter, extract_all
from release_impact_brief.adapters.java import JavaAdapter
from release_impact_brief.adapters.structure import StructureAdapter
from release_impact_brief.connections import add_connections
from release_impact_brief.system import add_services


def analyze(root: Path, skip: Iterable[str] = ()) -> Extraction:
    """All facts for a repo: code, build/deploy files, services, then their connections."""
    adapters: list[LanguageAdapter] = [JavaAdapter(), StructureAdapter()]
    out = extract_all(root, adapters, skip)
    add_services(out)
    add_connections(out)
    return out
