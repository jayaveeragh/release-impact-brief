"""Build files -> module facts: Maven `pom.xml` and Gradle settings / build scripts.

One module node per directory (`build:module:<dir>`, `.` for the repo root). A
directory with both a pom.xml and a build.gradle stays one module listing both
tools. Maven is parsed as XML (exact); Gradle scripts are code we never run, so
they are matched as text and their edges are `likely`.
"""

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any
from xml.etree.ElementTree import Element

from defusedxml import ElementTree

from release_impact_brief.adapters import Extraction
from release_impact_brief.facts import Confidence, Edge, EdgeKind, Node, NodeKind

_RESOLVER = "build-files"
_GRADLE_INCLUDE = re.compile(r"^\s*include\b(.*)$", re.M)
_QUOTED = re.compile(r"""['"]([^'"]+)['"]""")
_GRADLE_ROOT_NAME = re.compile(r"""rootProject\.name\s*=\s*['"]([^'"]+)['"]""")
_GRADLE_PROJECT = re.compile(r"""project\(\s*(?:path\s*[:=]\s*)?['"](:[^'"]*)['"]""")
_GRADLE_PLUGIN = re.compile(r"""\bid\s*\(?\s*['"]([\w.\-]+)['"]""")
# External dependency in a non-test configuration: implementation 'group:artifact:version'.
_GRADLE_DEP = re.compile(
    r"""^\s*(?:implementation|api|compileOnly|runtimeOnly|annotationProcessor|developmentOnly)"""
    r"""\b\W*['"]([\w.\-]+):([\w.\-]+)""",
    re.M,
)


def line_of(text: str, needle: str, start: int = 0) -> int:
    """1-based line of the first `needle` at or after `start`; 1 if not found."""
    index = text.find(needle, max(start, 0))
    return text.count("\n", 0, index) + 1 if index >= 0 else 1


def line_count(text: str) -> int:
    return max(1, len(text.splitlines()))


def _dir(rel: str) -> str:
    return posixpath.dirname(rel) or "."


def _join(base: str, sub: str) -> str:
    return posixpath.normpath(posixpath.join(base, sub))


def _text(el: Element, path: str) -> str | None:
    found = el.find(path)
    return found.text.strip() if found is not None and found.text else None


@dataclass
class _Ref:
    kind: EdgeKind
    target: str  # "dir:<path>", "ga:<group:artifact>" or "gradle:<:project:path>"
    confidence: Confidence
    file: str
    line: int


@dataclass
class _Module:
    dir: str
    file: str
    language: str
    end_line: int
    attributes: dict[str, Any]
    refs: list[_Ref] = field(default_factory=list)


class BuildIndex:
    """Collects modules from all build files, then links them once every file is read."""

    def __init__(self) -> None:
        self.modules: dict[str, _Module] = {}
        self.gradle_roots: set[str] = set()

    def _add(self, module: _Module) -> None:
        existing = self.modules.get(module.dir)
        if existing is None:
            self.modules[module.dir] = module
            return
        a, b = existing.attributes, module.attributes
        a["build_tools"] += [t for t in b["build_tools"] if t not in a["build_tools"]]
        for key in ("modules", "dependencies", "plugins"):
            a[key] += [v for v in b.get(key, []) if v not in a[key]]
        existing.refs += module.refs

    def maven(self, rel: str, text: str) -> None:
        root = ElementTree.fromstring(text)  # refuses DTDs / entities
        for el in root.iter():
            el.tag = el.tag.rsplit("}", 1)[-1]  # drop the POM namespace
        directory = _dir(rel)
        parent_group = _text(root, "parent/groupId") or ""
        parent_artifact = _text(root, "parent/artifactId")
        group = _text(root, "groupId") or parent_group
        artifact = _text(root, "artifactId") or posixpath.basename(directory)
        refs: list[_Ref] = []
        if parent_artifact:
            refs.append(_Ref(EdgeKind.EXTENDS, f"ga:{parent_group}:{parent_artifact}",
                             Confidence.EXACT, rel, line_of(text, "<parent>")))  # fmt: skip
        modules = [m.text.strip() for m in root.findall("modules/module") if m.text]
        for m in modules:
            refs.append(_Ref(EdgeKind.CONTAINS, f"dir:{_join(directory, m)}", Confidence.EXACT,
                             rel, line_of(text, f"<module>{m}</module>")))  # fmt: skip
        deps_start = text.find("</dependencyManagement>")
        dependencies = []
        for dep in root.findall("dependencies/dependency"):
            if _text(dep, "scope") == "test":
                continue
            dep_group = (_text(dep, "groupId") or "").replace("${project.groupId}", group)
            dep_group = dep_group.replace("${project.parent.groupId}", parent_group)
            dep_artifact = _text(dep, "artifactId") or ""
            dependencies.append(f"{dep_group}:{dep_artifact}")
            line = line_of(text, f"<artifactId>{dep_artifact}</artifactId>", deps_start)
            refs.append(_Ref(EdgeKind.DEPENDS_ON, f"ga:{dep_group}:{dep_artifact}",
                             Confidence.EXACT, rel, line))  # fmt: skip
        plugins = [a for p in root.findall("build/plugins/plugin") if (a := _text(p, "artifactId"))]
        attributes = {
            "name": artifact,
            "build_tools": ["maven"],
            "group_id": group,
            "artifact_id": artifact,
            "packaging": _text(root, "packaging") or "jar",
            "parent": f"{parent_group}:{parent_artifact}" if parent_artifact else None,
            "modules": modules,
            "dependencies": dependencies,
            "plugins": plugins,
        }
        self._add(_Module(directory, rel, "maven", line_count(text), attributes, refs))

    def gradle_settings(self, rel: str, text: str) -> None:
        directory = _dir(rel)
        self.gradle_roots.add(directory)
        modules, refs = [], []
        for match in _GRADLE_INCLUDE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            for path in _QUOTED.findall(match.group(1)):
                sub = path.lstrip(":").replace(":", "/")
                modules.append(sub)
                refs.append(_Ref(EdgeKind.CONTAINS, f"dir:{_join(directory, sub)}",
                                 Confidence.LIKELY, rel, line))  # fmt: skip
        name = _GRADLE_ROOT_NAME.search(text)
        self._add(_Module(directory, rel, "gradle", line_count(text), {
            "name": name.group(1) if name else posixpath.basename(directory),
            "build_tools": ["gradle"],
            "modules": modules,
            "dependencies": [],
            "plugins": [],
        }, refs))  # fmt: skip

    def gradle_build(self, rel: str, text: str) -> None:
        directory = _dir(rel)
        refs = [
            _Ref(EdgeKind.DEPENDS_ON, f"gradle:{m.group(1)}", Confidence.LIKELY, rel,
                 text.count("\n", 0, m.start()) + 1)
            for m in _GRADLE_PROJECT.finditer(text)
        ]  # fmt: skip
        self._add(_Module(directory, rel, "gradle", line_count(text), {
            "name": posixpath.basename(directory),
            "build_tools": ["gradle"],
            "modules": [],
            "dependencies": [f"{g}:{a}" for g, a in _GRADLE_DEP.findall(text)],
            "plugins": _GRADLE_PLUGIN.findall(text),
        }, refs))  # fmt: skip

    def _gradle_root(self, directory: str) -> str:
        while directory not in self.gradle_roots and directory != ".":
            directory = _dir(directory)
        return directory

    def _target(self, module: _Module, target: str, by_ga: dict[str, str]) -> str | None:
        kind, _, value = target.partition(":")
        if kind == "dir":
            return value
        if kind == "ga":
            return by_ga.get(value)
        return _join(self._gradle_root(module.dir), value.lstrip(":").replace(":", "/") or ".")

    def finish(self, out: Extraction) -> None:
        by_ga = {
            f"{m.attributes['group_id']}:{m.attributes['artifact_id']}": m.dir
            for m in self.modules.values()
            if "artifact_id" in m.attributes
        }
        edges: dict[str, Edge] = {}
        for m in sorted(self.modules.values(), key=lambda m: m.dir):
            node_id = f"build:module:{m.dir}"
            out.nodes.append(Node(node_id, NodeKind.MODULE, m.attributes["name"], m.file, 1,
                                  m.end_line, m.language, m.attributes))  # fmt: skip
            for ref in m.refs:
                target = self._target(m, ref.target, by_ga)
                if target is None or target == m.dir or target not in self.modules:
                    continue  # library, or a module without a build file we could read
                dst = f"build:module:{target}"
                edge_id = f"{ref.kind}:{node_id}>{dst}"
                edges.setdefault(edge_id, Edge(edge_id, node_id, dst, ref.kind, ref.confidence,
                                               ref.file, ref.line, _RESOLVER))  # fmt: skip
        out.edges += edges.values()
