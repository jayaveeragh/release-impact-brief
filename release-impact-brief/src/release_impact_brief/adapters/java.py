"""Java adapter: syntax-only facts from Tree-sitter (no type resolution yet).

Fact IDs are readable and stable across runs:
  java:class:<package>.<Outer>.<Inner>
  java:method:<class>#<name>(<param types as written>)
  java:field:<class>#<name>
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import tree_sitter_java
from tree_sitter import Language, Parser
from tree_sitter import Node as TsNode

from release_impact_brief.adapters import Extraction
from release_impact_brief.facts import Confidence, Edge, Node, NodeKind

_LANGUAGE = Language(tree_sitter_java.language())
_RESOLVER = "java-syntax"

_TYPE_DECLS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}
_METHOD_DECLS = {
    "method_declaration",
    "constructor_declaration",
    "compact_constructor_declaration",
    "annotation_type_element_declaration",
}
_FIELD_DECLS = {"field_declaration", "constant_declaration"}


def _text(node: TsNode | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text else ""


def _compact(text: str) -> str:
    return "".join(text.split())


def _modifiers(decl: TsNode) -> tuple[list[str], list[str]]:
    """Return (modifier keywords, annotation names) of a declaration or parameter."""
    keywords: list[str] = []
    annotations: list[str] = []
    for child in decl.children:
        if child.type != "modifiers":
            continue
        for mod in child.children:
            if mod.type in ("marker_annotation", "annotation"):
                annotations.append(_text(mod.child_by_field_name("name")))
            else:
                keywords.append(_text(mod))
    return keywords, annotations


def _type_list(node: TsNode | None) -> list[str]:
    if node is None:
        return []
    for child in node.named_children:
        if child.type == "type_list":
            return [_compact(_text(t)) for t in child.named_children]
    return [_compact(_text(t)) for t in node.named_children]


def _params(params: TsNode | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in params.named_children if params is not None else []:
        _, annotations = _modifiers(p)
        if p.type == "formal_parameter":
            name = _text(p.child_by_field_name("name"))
            type_ = _compact(_text(p.child_by_field_name("type")))
        elif p.type == "spread_parameter":
            types = [
                c for c in p.named_children if c.type not in ("modifiers", "variable_declarator")
            ]
            decl = next(c for c in p.named_children if c.type == "variable_declarator")
            name = _text(decl.child_by_field_name("name"))
            type_ = _compact(_text(types[0])) + "..."
        else:
            continue
        out.append({"name": name, "type": type_, "annotations": annotations})
    return out


class _FileExtractor:
    def __init__(self, rel: str, tree_root: TsNode, out: Extraction, seen: set[str]) -> None:
        self.rel = rel
        self.out = out
        self.seen = seen
        self.package = ""
        self.imports: list[str] = []
        for child in tree_root.named_children:
            if child.type == "package_declaration":
                self.package = next(
                    _text(c)
                    for c in child.named_children
                    if c.type in ("identifier", "scoped_identifier")
                )
            elif child.type == "import_declaration":
                self.imports.append(
                    " ".join(_text(child).removeprefix("import").rstrip(";").split())
                )
        for child in tree_root.named_children:
            if child.type in _TYPE_DECLS:
                self.type_decl(child, self.package, None)

    def _unique(self, node_id: str) -> str:
        # Same qualified name in two files (e.g. two modules): keep both, disambiguate by file.
        if node_id in self.seen:
            node_id = f"{node_id}@{self.rel}"
        self.seen.add(node_id)
        return node_id

    def _add(self, node: Node, parent_id: str | None, line: int) -> None:
        self.out.nodes.append(node)
        if parent_id is not None:
            self.out.edges.append(
                Edge(
                    id=f"contains:{parent_id}>{node.id}",
                    src=parent_id,
                    dst=node.id,
                    kind="contains",
                    confidence=Confidence.EXACT,
                    evidence_file=self.rel,
                    evidence_line=line,
                    resolver=_RESOLVER,
                )
            )

    def _lines(self, node: TsNode) -> tuple[int, int]:
        return node.start_point.row + 1, node.end_point.row + 1

    def type_decl(self, decl: TsNode, scope: str, parent_id: str | None) -> None:
        name = _text(decl.child_by_field_name("name"))
        qualified = f"{scope}.{name}" if scope else name
        class_id = self._unique(f"java:class:{qualified}")
        keywords, annotations = _modifiers(decl)
        extends = _type_list(decl.child_by_field_name("superclass"))
        if decl.type == "interface_declaration":
            extends = _type_list(
                next((c for c in decl.children if c.type == "extends_interfaces"), None)
            )
        start, end = self._lines(decl)
        attributes: dict[str, Any] = {
            "qualified_name": qualified,
            "type_kind": _TYPE_DECLS[decl.type],
            "package": self.package,
            "modifiers": keywords,
            "annotations": annotations,
            "extends": extends,
            "implements": _type_list(decl.child_by_field_name("interfaces")),
        }
        if parent_id is None:
            attributes["imports"] = self.imports
        self._add(
            Node(class_id, NodeKind.CLASS, name, self.rel, start, end, "java", attributes),
            parent_id,
            start,
        )

        if decl.type == "record_declaration":
            for p in _params(decl.child_by_field_name("parameters")):
                self._field(class_id, p["name"], p["type"], [], p["annotations"], decl, record=True)

        body = decl.child_by_field_name("body")
        members = list(body.named_children) if body is not None else []
        for member in list(members):
            if member.type == "enum_body_declarations":
                members += member.named_children
        for member in members:
            if member.type in _TYPE_DECLS:
                self.type_decl(member, qualified, class_id)
            elif member.type in _METHOD_DECLS:
                self.method(member, class_id, name)
            elif member.type in _FIELD_DECLS:
                keywords, annotations = _modifiers(member)
                type_ = _compact(_text(member.child_by_field_name("type")))
                for declarator in member.children_by_field_name("declarator"):
                    field_name = _text(declarator.child_by_field_name("name"))
                    self._field(class_id, field_name, type_, keywords, annotations, member)
            elif member.type == "enum_constant":
                const = _text(member.child_by_field_name("name"))
                self._field(class_id, const, name, [], [], member, enum_constant=True)

    def method(self, decl: TsNode, class_id: str, class_name: str) -> None:
        is_constructor = decl.type in ("constructor_declaration", "compact_constructor_declaration")
        name = _text(decl.child_by_field_name("name"))
        params = _params(decl.child_by_field_name("parameters"))
        signature = ",".join(p["type"] for p in params)
        owner = class_id.removeprefix("java:class:")
        method_id = self._unique(f"java:method:{owner}#{name}({signature})")
        keywords, annotations = _modifiers(decl)
        start, end = self._lines(decl)
        attributes: dict[str, Any] = {
            "return_type": None
            if is_constructor
            else _compact(_text(decl.child_by_field_name("type"))),
            "parameters": params,
            "modifiers": keywords,
            "annotations": annotations,
            "constructor": is_constructor,
        }
        self._add(
            Node(method_id, NodeKind.METHOD, name, self.rel, start, end, "java", attributes),
            class_id,
            start,
        )

    def _field(
        self,
        class_id: str,
        name: str,
        type_: str,
        keywords: list[str],
        annotations: list[str],
        decl: TsNode,
        *,
        record: bool = False,
        enum_constant: bool = False,
    ) -> None:
        owner = class_id.removeprefix("java:class:")
        field_id = self._unique(f"java:field:{owner}#{name}")
        start, end = self._lines(decl)
        attributes: dict[str, Any] = {
            "type": type_,
            "modifiers": keywords,
            "annotations": annotations,
        }
        if record:
            attributes["record_component"] = True
        if enum_constant:
            attributes["enum_constant"] = True
        self._add(
            Node(field_id, NodeKind.FIELD, name, self.rel, start, end, "java", attributes),
            class_id,
            start,
        )


class JavaAdapter:
    language: str = "java"
    extensions: tuple[str, ...] = (".java",)

    def __init__(self) -> None:
        self._parser = Parser(_LANGUAGE)

    def extract(self, root: Path, files: Sequence[str]) -> Extraction:
        out = Extraction()
        seen: set[str] = set()
        for rel in files:
            tree = self._parser.parse((root / rel).read_bytes())
            if tree.root_node.has_error:
                out.files_with_errors.append(rel)
            _FileExtractor(rel, tree.root_node, out, seen)
            out.files_parsed += 1
        return out
