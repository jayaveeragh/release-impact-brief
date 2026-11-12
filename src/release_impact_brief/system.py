"""System structure on top of code and build facts: services and repo classification.

A service is a `@SpringBootApplication` class in main code (not tests), placed in
the build module whose directory holds it. Its name comes from
`spring.application.name` in that module's default config, else the module, else
the class. Classification is computed from facts, never stored, and every verdict
carries the evidence (file and line) it was based on; conflicting signals give
"unclear" instead of a guess.
"""

import posixpath
import re
from collections.abc import Iterable
from typing import Any

from release_impact_brief.adapters import Extraction
from release_impact_brief.facts import Confidence, Edge, EdgeKind, Node, NodeKind

_RESOLVER = "system"
_ROLE_ANNOTATIONS = {
    "EnableEurekaServer": "discovery_server",
    "EnableConfigServer": "config_server",
    "EnableAdminServer": "admin_server",
    "EnableDiscoveryClient": "discovery_client",
    "EnableEurekaClient": "discovery_client",
}
_DISCOVERY_CLIENT_DEPS = ("-eureka-client", "-consul-discovery")
_GATEWAY_ROUTE = re.compile(r"^spring\.cloud\.gateway\..*routes\[\d+\]")
_PLACEHOLDER_DEFAULT = re.compile(r"\$\{[^:}]*:([^}]*)\}")
_INFRASTRUCTURE = {
    "discovery_server": "service discovery server",
    "config_server": "config server",
    "gateway": "gateway routes",
    "discovery_client": "discovery client",
}


def is_test(path: str) -> bool:
    return "/src/test/" in f"/{path}"


def module_dir(path: str, module_dirs: Iterable[str]) -> str | None:
    """Directory of the innermost module holding `path`."""
    holding = [d for d in module_dirs if d == "." or path.startswith(f"{d}/")]
    return max(holding, key=lambda d: 0 if d == "." else len(d), default=None)


def _evidence(text: str, file: str | None, line: int | None = None) -> dict[str, Any]:
    return {"text": text, "file": file, "line": line}


def properties(config: Node) -> list[tuple[str, dict[str, Any]]]:
    """(key, {value, line}) for every property in every document of a Spring config."""
    return [(k, v) for doc in config.attributes["documents"] for k, v in doc["properties"].items()]


def _default(configs: list[Node], key: str) -> dict[str, Any] | None:
    """A property from the default (no profile) config's first document."""
    for config in configs:
        documents = config.attributes["documents"]
        if config.attributes["profile"] is None and documents and key in documents[0]["properties"]:
            return dict(documents[0]["properties"][key])
    return None


def _roles(main: Node, module: Node | None, configs: list[Node]) -> dict[str, dict[str, Any]]:
    """Role -> where it was seen ({file, line}); the first sighting is kept."""
    roles: dict[str, dict[str, Any]] = {}
    for a in main.attributes["annotations"]:
        if (role := _ROLE_ANNOTATIONS.get(a)) is not None:
            roles.setdefault(role, {"file": main.file, "line": main.start_line})
    dependencies = module.attributes.get("dependencies", []) if module else []
    if module is not None and any(d.endswith(_DISCOVERY_CLIENT_DEPS) for d in dependencies):
        roles.setdefault("discovery_client", {"file": module.file, "line": 1})
    for config in configs:
        for key, prop in properties(config):
            role = None
            if _GATEWAY_ROUTE.match(key):
                role = "gateway"
            elif key == "spring.cloud.config.uri" or (
                key == "spring.config.import" and "configserver:" in prop["value"]
            ):
                role = "config_client"
            if role is not None:
                roles.setdefault(role, {"file": config.file, "line": prop["line"]})
    return dict(sorted(roles.items()))


def _deployed_by(name: str, directory: str | None, deploys: list[Node]) -> list[str]:
    """Compose services / Kubernetes workloads that look like this service (by name)."""
    found = []
    for d in deploys:
        a = d.attributes
        images = [a["image"]] if a.get("image") else a.get("images", [])
        image_names = {str(i).rsplit("/", 1)[-1].split(":", 1)[0] for i in images}
        build = a.get("build")
        build_dir = posixpath.normpath(posixpath.join(posixpath.dirname(d.file), str(build)))
        built_here = bool(build) and build_dir == directory
        if d.name == name or name in image_names or built_here:
            found.append(d.id)
    return sorted(found)


def add_services(out: Extraction) -> None:
    modules = {
        n.id.removeprefix("build:module:"): n for n in out.nodes if n.kind == NodeKind.MODULE
    }
    configs: dict[str | None, list[Node]] = {}
    deploys: list[Node] = []
    for n in out.nodes:
        if n.kind != NodeKind.CONFIG:
            continue
        if n.attributes["config_type"] == "spring" and not n.attributes["test"]:
            configs.setdefault(module_dir(n.file, modules), []).append(n)
        elif n.attributes["config_type"] == "compose_service" or (
            n.attributes["config_type"] == "kubernetes"
            and n.attributes["k8s_kind"] in ("Deployment", "StatefulSet", "DaemonSet")
        ):
            deploys.append(n)
    mains = [
        n
        for n in out.nodes
        if n.kind == NodeKind.CLASS
        and "SpringBootApplication" in n.attributes["annotations"]
        and not is_test(n.file)
    ]
    seen: set[str] = set()
    for main in sorted(mains, key=lambda n: n.id):
        directory = module_dir(main.file, modules)
        module = modules.get(directory) if directory is not None else None
        own = configs.get(directory, [])
        app_name = _default(own, "spring.application.name")
        if app_name and app_name["value"]:
            name, source = str(app_name["value"]), "spring.application.name"
        elif module is not None:
            name, source = module.name, "module"
        else:
            name, source = main.name, "class"
        node_id = f"service:{name}"
        if node_id in seen:
            node_id += f"@{main.file}"
        seen.add(node_id)
        port = _default(own, "server.port")
        roles = _roles(main, module, own)
        out.nodes.append(Node(node_id, NodeKind.SERVICE, name, main.file, main.start_line,
                              main.end_line, main.language, {
                                  "main_class": main.id,
                                  "module": module.id if module else None,
                                  "name_source": source,
                                  "port": port["value"] if port else None,
                                  "roles": list(roles),
                                  "role_evidence": roles,
                                  "deployed_by": _deployed_by(name, directory, deploys),
                              }))  # fmt: skip
        if module is not None:
            out.edges.append(Edge(f"contains:{node_id}>{module.id}", node_id, module.id,
                                  EdgeKind.CONTAINS, Confidence.EXACT, main.file,
                                  main.start_line, _RESOLVER))  # fmt: skip


def datasource_url(value: str) -> str:
    """Comparable datasource URL: placeholder defaults filled in, query string dropped."""
    return _PLACEHOLDER_DEFAULT.sub(r"\1", value).split("?", 1)[0]


def _layout(modules: list[Node], edges: list[Edge]) -> dict[str, Any]:
    if not modules:
        return {"value": "unclear", "evidence": [_evidence("no Maven or Gradle build file", None)]}
    ids = {m.id for m in modules}
    children = {e.dst for e in edges if e.kind == EdgeKind.CONTAINS and e.src in ids}
    roots = [m for m in modules if m.id not in children]
    if len(roots) > 1:
        return {"value": "monorepo", "evidence": [
            _evidence(f"independent build at {m.id.removeprefix('build:module:')} "
                      f"({', '.join(m.attributes['build_tools'])})", m.file, 1)
            for m in roots
        ]}  # fmt: skip
    root = roots[0]
    tools = ", ".join(root.attributes["build_tools"])
    if len(modules) > 1:
        return {"value": "multi-module", "evidence": [
            _evidence(f"{root.file} ({tools}) aggregates {len(modules) - 1} modules", root.file, 1)
        ]}  # fmt: skip
    return {"value": "single project",
            "evidence": [_evidence(f"one build: {root.file} ({tools})", root.file, 1)]}  # fmt: skip


def classify(nodes: Iterable[Node], edges: Iterable[Edge]) -> dict[str, Any]:
    """Repo layout and runtime style, each with the evidence behind it."""
    nodes, edges = list(nodes), list(edges)
    modules = [n for n in nodes if n.kind == NodeKind.MODULE]
    services = [n for n in nodes if n.kind == NodeKind.SERVICE]
    layout = _layout(modules, edges)

    evidence = [
        _evidence(f"Spring Boot app {s.name} ({s.attributes['main_class'].rsplit('.', 1)[-1]})",
                  s.file, s.start_line)
        for s in services
    ]  # fmt: skip
    if not services:
        value = "library" if modules else "unclear"
        evidence.append(_evidence("no @SpringBootApplication class in main code", None))
        if modules:
            evidence[-1]["file"] = modules[0].file
        return {"layout": layout, "runtime": {"value": value, "evidence": evidence}}
    if len(services) == 1:
        value = "modular monolith" if layout["value"] == "multi-module" else "monolith"
        return {"layout": layout, "runtime": {"value": value, "evidence": evidence}}

    signals = []
    for s in services:
        for role, seen in s.attributes["role_evidence"].items():
            if role in _INFRASTRUCTURE:
                signals.append(_evidence(f"{s.name}: {_INFRASTRUCTURE[role]}", seen["file"],
                                         seen["line"]))  # fmt: skip
    deployed = [s for s in services if s.attributes["deployed_by"]]
    if len(deployed) > 1:
        first = deployed[0].attributes["deployed_by"][0]
        file = next(n.file for n in nodes if n.id == first)
        evidence.append(_evidence(f"{len(deployed)} apps deployed as separate containers", file))

    # Several apps reading one database point to one system split for deployment.
    service_of = {s.attributes["module"]: s.name for s in services if s.attributes["module"]}
    users: dict[str, set[str]] = {}
    where: dict[str, tuple[str, int]] = {}
    for n in nodes:
        if n.kind != NodeKind.CONFIG or n.attributes["config_type"] != "spring":
            continue
        if n.attributes["test"]:
            continue
        owner = module_dir(n.file, [m.removeprefix("build:module:") for m in service_of])
        if owner is None:
            continue
        for key, prop in properties(n):
            if key == "spring.datasource.url" and prop["value"]:
                url = datasource_url(str(prop["value"]))
                users.setdefault(url, set()).add(service_of[f"build:module:{owner}"])
                where.setdefault(url, (n.file, prop["line"]))
    against = [
        _evidence(f"{', '.join(sorted(names))} share database {url}", *where[url])
        for url, names in sorted(users.items())
        if len(names) > 1
    ]
    if signals and not against:
        value = "microservices"
    else:
        value = "unclear"
        reason = ("service infrastructure, but apps share a database" if signals
                  else "several apps, but no discovery, gateway or config server")  # fmt: skip
        evidence.append(_evidence(f"signals conflict: {reason}", None))  # a conclusion, not a file
    return {"layout": layout,
            "runtime": {"value": value, "evidence": evidence + signals + against}}  # fmt: skip
