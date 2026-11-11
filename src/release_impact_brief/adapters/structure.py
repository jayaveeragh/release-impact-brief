"""Build, deployment and configuration files -> module and config facts.

Everything is read as data, never run: Gradle scripts are matched as text, YAML
is composed without constructing objects (and with a node budget against alias
bombs), XML refuses entities. Values of secret-looking keys are redacted, and
Kubernetes Secrets keep only their kind and name.

Fact IDs:
  build:module:<dir>
  deploy:dockerfile:<file>
  deploy:compose:<file>#<service>
  deploy:k8s:<file>#<Kind>/<name>
  config:spring:<file>
"""

import re
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree.ElementTree import ParseError

import yaml

from release_impact_brief.adapters import Extraction
from release_impact_brief.adapters.build_files import BuildIndex, line_count
from release_impact_brief.facts import Confidence, Edge, EdgeKind, Node, NodeKind

_RESOLVER = "structure"
_SPRING_CONFIG = re.compile(r"^(?:application|bootstrap)(?:-([\w.-]+))?\.(?:ya?ml|properties)$")
_SECRET = re.compile(r"passw|secret|token|credential|api[-_]?key|private[-_]?key", re.I)
_PLACEHOLDER = re.compile(r"^\$\{[^}]*\}$")
_MAX_YAML_NODES = 20_000
# Processing order: Maven before Gradle settings before Gradle builds, so a module's
# name and coordinates come from the most precise file.
_ORDER = ("maven", "gradle_settings", "gradle_build", "dockerfile", "compose", "spring", "yaml")


def _kind(rel: str) -> str | None:
    path = PurePosixPath(rel)
    name = path.name
    if name == "pom.xml":
        return "maven"
    if name in ("settings.gradle", "settings.gradle.kts"):
        return "gradle_settings"
    if name in ("build.gradle", "build.gradle.kts"):
        return "gradle_build"
    if name == "Dockerfile":
        return "dockerfile"
    if _SPRING_CONFIG.match(name):
        return "spring"
    if path.suffix in (".yml", ".yaml") and ".github" not in path.parts:
        return "compose" if "compose" in name.lower() else "yaml"
    return None


def _redact(key: str, value: str) -> str:
    last = key.rsplit(".", 1)[-1]
    if _SECRET.search(last) and value and not _PLACEHOLDER.match(value):
        return "<redacted>"
    return value


class _Budget:
    def __init__(self) -> None:
        self.left = _MAX_YAML_NODES

    def spend(self) -> None:
        self.left -= 1
        if self.left < 0:
            raise ValueError("YAML too large or too many aliases")


def _key(node: yaml.Node) -> str:
    return str(node.value) if isinstance(node, yaml.ScalarNode) else "?"


def _plain(node: yaml.Node, budget: _Budget) -> Any:
    """YAML node -> plain data; every scalar stays a string."""
    budget.spend()
    if isinstance(node, yaml.MappingNode):
        return {_key(k): _plain(v, budget) for k, v in node.value}
    if isinstance(node, yaml.SequenceNode):
        return [_plain(v, budget) for v in node.value]
    return str(node.value)


def _flatten(node: yaml.Node, prefix: str, out: dict[str, Any], budget: _Budget) -> None:
    """Spring-style flat keys (`spring.datasource.url`, `list[0]`) with their lines."""
    budget.spend()
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            _flatten(v, f"{prefix}.{_key(k)}" if prefix else _key(k), out, budget)
    elif isinstance(node, yaml.SequenceNode):
        for i, v in enumerate(node.value):
            _flatten(v, f"{prefix}[{i}]", out, budget)
    elif prefix:
        out[prefix] = {"value": _redact(prefix, str(node.value)),
                       "line": node.start_mark.line + 1}  # fmt: skip


def _lines(node: yaml.Node) -> tuple[int, int]:
    start = node.start_mark.line + 1
    return start, max(start, node.end_mark.line)


def _docs(text: str) -> list[yaml.Node]:
    return [d for d in yaml.compose_all(text, Loader=yaml.SafeLoader) if d is not None]


def _env(value: Any) -> dict[str, str]:
    if isinstance(value, list):  # ["KEY=value", ...]
        value = dict(item.partition("=")[::2] for item in value if isinstance(item, str))
    if not isinstance(value, dict):
        return {}
    return {str(k): _redact(str(k), str(v)) for k, v in value.items()}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _names(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k) for k in value]
    return [str(v) for v in value] if isinstance(value, list) else []


class StructureAdapter:
    language: str = "structure"
    # "" = files without an extension, e.g. Dockerfile. Files are then picked by name.
    extensions: tuple[str, ...] = (".xml", ".gradle", ".kts", ".yml", ".yaml", ".properties", "")

    def extract(self, root: Path, files: Sequence[str]) -> Extraction:
        out = Extraction()
        builds = BuildIndex()
        picked = [(k, rel) for rel in files if (k := _kind(rel)) is not None]
        for kind, rel in sorted(picked, key=lambda p: (_ORDER.index(p[0]), p[1])):
            text = (root / rel).read_text("utf-8", "replace")
            if kind == "yaml" and "templates" in PurePosixPath(rel).parts and "{{" in text:
                continue  # Helm template: not YAML until rendered
            try:
                match kind:
                    case "maven":
                        builds.maven(rel, text)
                    case "gradle_settings":
                        builds.gradle_settings(rel, text)
                    case "gradle_build":
                        builds.gradle_build(rel, text)
                    case "dockerfile":
                        _dockerfile(rel, text, out)
                    case "compose":
                        _compose(rel, text, out)
                    case "spring":
                        _spring(rel, text, out)
                    case "yaml":
                        _kubernetes(rel, text, out)
            except (ParseError, ValueError, yaml.YAMLError):  # defusedxml errors are ValueErrors
                out.files_with_errors.append(rel)
            out.files_parsed += 1
        builds.finish(out)
        return out


def _dockerfile(rel: str, text: str, out: Extraction) -> None:
    logical = re.sub(r"\\\n\s*", "", text).splitlines()  # join `\` continuations
    images, ports, entrypoint = [], [], None
    for line in logical:
        word, _, rest = line.strip().partition(" ")
        match word.upper():
            case "FROM":
                images.append(next((w for w in rest.split() if not w.startswith("--")), ""))
            case "EXPOSE":
                ports += [p.split("/")[0] for p in rest.split()]
            case "ENTRYPOINT" | "CMD" if entrypoint is None or word.upper() == "ENTRYPOINT":
                entrypoint = line.strip()
    out.nodes.append(Node(f"deploy:dockerfile:{rel}", NodeKind.CONFIG, rel, rel, 1,
                          line_count(text), "docker", {"config_type": "dockerfile",
                          "base_images": images, "exposed_ports": ports,
                          "entrypoint": entrypoint}))  # fmt: skip


def _compose(rel: str, text: str, out: Extraction) -> None:
    budget = _Budget()
    for doc in _docs(text)[:1]:
        if not isinstance(doc, yaml.MappingNode):
            return
        services = next((v for k, v in doc.value if _key(k) == "services"), None)
        if not isinstance(services, yaml.MappingNode):
            return
        names = {_key(k) for k, _ in services.value}
        for key, value in services.value:
            name = _key(key)
            spec = _plain(value, budget)
            spec = spec if isinstance(spec, dict) else {}
            build = spec.get("build")
            node_id = f"deploy:compose:{rel}#{name}"
            start, end = _lines(key)[0], _lines(value)[1]
            out.nodes.append(Node(node_id, NodeKind.CONFIG, name, rel, start, end, "compose", {
                "config_type": "compose_service",
                "image": spec.get("image"),
                "build": build.get("context") if isinstance(build, dict) else build,
                "ports": _names(spec.get("ports")),
                "depends_on": _names(spec.get("depends_on")),
                "environment": _env(spec.get("environment")),
            }))  # fmt: skip
            for dep in [d for d in _names(spec.get("depends_on")) if d in names]:
                dst = f"deploy:compose:{rel}#{dep}"
                out.edges.append(Edge(f"depends_on:{node_id}>{dst}", node_id, dst,
                                      EdgeKind.DEPENDS_ON, Confidence.EXACT, rel, start,
                                      _RESOLVER))  # fmt: skip


def _kubernetes(rel: str, text: str, out: Extraction) -> None:
    budget = _Budget()
    for doc in _docs(text):
        data = _plain(doc, budget)
        if not isinstance(data, dict) or "apiVersion" not in data or "kind" not in data:
            continue
        kind = str(data["kind"])
        meta = _dict(data.get("metadata"))
        name = str(meta.get("name", "?"))
        spec = _dict(data.get("spec"))
        pod = _dict(_dict(spec.get("template")).get("spec")) or spec
        containers = _dicts(pod.get("containers"))
        ports = [str(p["containerPort"]) for c in containers for p in _dicts(c.get("ports"))
                 if "containerPort" in p]  # fmt: skip
        if kind == "Service":
            ports = [str(p["port"]) for p in _dicts(spec.get("ports")) if "port" in p]
        start, end = _lines(doc)
        out.nodes.append(Node(f"deploy:k8s:{rel}#{kind}/{name}", NodeKind.CONFIG, name, rel,
                              start, end, "kubernetes", {
                                  "config_type": "kubernetes",
                                  "k8s_kind": kind,
                                  "namespace": meta.get("namespace"),
                                  "images": [str(c["image"]) for c in containers if "image" in c],
                                  "ports": ports,
                              }))  # fmt: skip


def _spring(rel: str, text: str, out: Extraction) -> None:
    documents: list[dict[str, Any]] = []
    if rel.endswith(".properties"):
        props: dict[str, Any] = {}
        documents.append({"profile": None, "properties": props})
        for number, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if line == "#---":  # Spring Boot multi-document properties
                props = {}
                documents.append({"profile": None, "properties": props})
            if not line or line[0] in "#!":
                continue
            parts = re.split(r"\s*[=:]\s*", line, maxsplit=1)
            key, value = parts[0], parts[1] if len(parts) > 1 else ""
            props[key] = {"value": _redact(key, value), "line": number}
    else:
        budget = _Budget()
        for doc in _docs(text):
            props = {}
            _flatten(doc, "", props, budget)
            documents.append({"profile": None, "properties": props})
    for document in documents:
        props = document["properties"]
        profile = props.get("spring.config.activate.on-profile") or props.get("spring.profiles")
        document["profile"] = profile["value"] if profile else None
    match = _SPRING_CONFIG.match(PurePosixPath(rel).name)
    out.nodes.append(Node(f"config:spring:{rel}", NodeKind.CONFIG, rel, rel, 1, line_count(text),
                          "spring-config", {
                              "config_type": "spring",
                              "profile": match.group(1) if match else None,
                              "test": "/src/test/" in f"/{rel}",
                              "documents": documents,
                          }))  # fmt: skip
