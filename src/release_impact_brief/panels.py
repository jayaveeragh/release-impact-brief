"""Panel data for the web app, straight from facts (no LLM): services, modules,
endpoints, data model, data stores, outside systems and unclear items. Every row
keeps its file and line so the UI can link to the source on GitHub."""

from typing import Any

from release_impact_brief.adapters import Extraction
from release_impact_brief.facts import EdgeKind, Node, NodeKind
from release_impact_brief.summaries import SummaryResult
from release_impact_brief.system import module_dir, unclear_items


def _short(node_id: str) -> str:
    return node_id.split(":", 2)[-1]


def _where(n: Node) -> dict[str, Any]:
    return {"file": n.file, "line": n.start_line}


def build_panels(facts: Extraction, summaries: SummaryResult) -> dict[str, Any]:
    nodes = {n.id: n for n in facts.nodes}
    by_kind: dict[NodeKind, list[Node]] = {}
    for n in sorted(facts.nodes, key=lambda n: n.id):
        by_kind.setdefault(n.kind, []).append(n)
    modules = by_kind.get(NodeKind.MODULE, [])
    dirs = [m.id.removeprefix("build:module:") for m in modules]
    service_of_module = {s.attributes["module"]: s for s in by_kind.get(NodeKind.SERVICE, [])
                         if s.attributes["module"]}  # fmt: skip

    def service_for(file: str) -> str | None:
        directory = module_dir(file, dirs)
        service = service_of_module.get(f"build:module:{directory}")
        return service.id if service else None

    def users(target: str) -> list[dict[str, Any]]:
        return [{"service": e.src, "kind": e.kind, "confidence": e.confidence,
                 "file": e.evidence_file, "line": e.evidence_line}
                for e in facts.edges
                if e.dst == target and e.src.startswith("service:")]  # fmt: skip

    module_summary = {k: v.data for k, v in summaries.modules.items() if v.data is not None}
    classes_of: dict[str, list[dict[str, Any]]] = {}
    for c in summaries.classes.values():
        node = nodes.get(c.unit_id)
        classes_of.setdefault(c.module or "", []).append({
            "id": c.unit_id, "name": c.name, "role": (c.data or {}).get("role"),
            "summary": (c.data or {}).get("summary"), "error": c.error,
            "file": node.file if node else None,
            "line": node.start_line if node else None})  # fmt: skip
    relations: dict[str, list[dict[str, Any]]] = {}
    repositories: dict[str, list[str]] = {}
    entity_of_class = {e.src: e.dst for e in facts.edges if e.kind == EdgeKind.MAPS}
    owner_of_field = {e.dst: e.src for e in facts.edges if e.kind == EdgeKind.CONTAINS}
    for e in facts.edges:
        if e.kind == EdgeKind.RELATES and (
            src := entity_of_class.get(owner_of_field.get(e.src, ""))
        ):
            field = nodes[e.src]
            relations.setdefault(src, []).append({
                "field": field.name, "kind": field.attributes["relation"]["kind"],
                "target": e.dst, "confidence": e.confidence, **_where(field)})  # fmt: skip
        elif e.kind == EdgeKind.STORES:
            repositories.setdefault(e.dst, []).append(_short(e.src))

    return {
        "services": [{
            "id": s.id, "name": s.name, "module": s.attributes["module"],
            "port": s.attributes["port"],
            "roles": s.attributes["roles"], "deployed_by": s.attributes["deployed_by"],
            "summary": (module_summary.get(s.attributes["module"]) or {}).get("summary"),
            **_where(s)} for s in by_kind.get(NodeKind.SERVICE, [])],
        "modules": [{
            "id": m.id, "name": m.name, "build_tools": m.attributes["build_tools"],
            "summary": (module_summary.get(m.id) or {}).get("summary"),
            "capabilities": (module_summary.get(m.id) or {}).get("capabilities", []),
            "classes": classes_of.get(m.id, []),
            "depends_on": sorted(e.dst for e in facts.edges
                                 if e.kind == EdgeKind.DEPENDS_ON and e.src == m.id),
            "service": service.id if (service := service_of_module.get(m.id)) else None,
            **_where(m)} for m in modules],
        "endpoints": [{
            "id": n.id, "method": n.attributes["http_method"], "path": n.attributes["path"],
            "path_resolved": n.attributes["path_resolved"],
            "handler": _short(n.attributes["handler"]), "service": service_for(n.file),
            "inputs": n.attributes["inputs"], "returns": n.attributes["returns"],
            "response": n.attributes["response"],
            "branches": len((nodes[n.attributes["handler"]].attributes.get("flow") or {})
                            .get("branches", [])),
            **_where(n)}
            for n in by_kind.get(NodeKind.ENDPOINT, [])],
        "entities": [{
            "id": n.id, "table": n.name, "class": _short(n.attributes["class"]),
            "table_source": n.attributes["table_source"],
            "relations": relations.get(n.id, []), "repositories": repositories.get(n.id, []),
            **_where(n)} for n in by_kind.get(NodeKind.ENTITY, [])],
        "data_stores": [{
            "id": n.id, "name": n.name, "locations": n.attributes["locations"],
            "used_by": users(n.id), **_where(n)} for n in by_kind.get(NodeKind.DATA_STORE, [])],
        "external_systems": [{
            "id": n.id, "name": n.name, "locations": n.attributes["locations"],
            "used_by": users(n.id), **_where(n)}
            for n in by_kind.get(NodeKind.EXTERNAL_SYSTEM, [])],
        "unclear": unclear_items(facts.nodes, facts.edges),
    }  # fmt: skip
