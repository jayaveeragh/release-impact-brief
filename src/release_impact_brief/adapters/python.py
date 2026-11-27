"""Python adapter: syntax-only facts from Tree-sitter, in the same shape as the Java ones.

Fact IDs are readable and stable across runs:
  python:class:<module>            a module (`type_kind` "module"); holds its functions
  python:class:<module>.<Class>    a class
  python:method:<owner>#<name>(<parameter names>)
  python:field:<owner>#<name>      a class attribute, `self.x` set in __init__, or a
                                   module-level variable
A module is a class-like unit so everything above (summaries, impact, chat) can treat
"the class a function belongs to" the same for both languages. Decorators are kept as
`annotations`; nothing is imported or run.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import tree_sitter_python
from tree_sitter import Language, Parser
from tree_sitter import Node as TsNode

from release_impact_brief.adapters import Extraction
from release_impact_brief.adapters.python_calls import PyCallSite, PythonResolver
from release_impact_brief.adapters.python_flow import extract_flow
from release_impact_brief.adapters.python_text import call_args, compact, string, text
from release_impact_brief.adapters.python_web import add_web
from release_impact_brief.facts import Confidence, Edge, EdgeKind, Node, NodeKind

_LANGUAGE = Language(tree_sitter_python.language())
_RESOLVER = "python-syntax"
# Calls whose result is a web app or router; recorded wherever they are assigned, so
# routes can be joined to their prefixes later (python_web.py).
_APP_CALLS = {"FastAPI", "APIRouter", "Flask", "Blueprint"}
_WIRING = {"include_router", "register_blueprint"}
_MAX_VALUE = 200


def module_name(rel: str) -> str:
    """`backend/app/api/main.py` -> `backend.app.api.main`; `__init__` is the package."""
    parts = rel.removesuffix(".py").split("/")
    if parts[-1] == "__init__" and len(parts) > 1:
        parts = parts[:-1]
    return ".".join(parts)


def chain(node: TsNode | None) -> str | None:
    """`sa.select(User).where(x)` -> `sa.select().where()`: the shape of a receiver, with
    arguments dropped; None when it starts from something else (a literal, a lambda)."""
    if node is None:
        return None
    match node.type:
        case "identifier":
            return text(node)
        case "attribute":
            inner = chain(node.child_by_field_name("object"))
            attr = text(node.child_by_field_name("attribute"))
            return f"{inner}.{attr}" if inner is not None else None
        case "call":
            inner = chain(node.child_by_field_name("function"))
            return f"{inner}()" if inner is not None else None
        case "subscript":
            inner = chain(node.child_by_field_name("value"))
            return f"{inner}[]" if inner is not None else None
        case "parenthesized_expression" if node.named_children:
            return chain(node.named_children[0])
    return None


def _decorators(decorated: TsNode | None) -> list[dict[str, Any]]:
    found = []
    for d in decorated.named_children if decorated is not None else []:
        if d.type != "decorator" or not d.named_children:
            continue
        expr = d.named_children[0]
        if expr.type == "call":
            args, kwargs = call_args(expr)
            name = text(expr.child_by_field_name("function"))
        else:
            args, kwargs, name = [], {}, text(expr)
        found.append({"name": compact(name), "args": args, "kwargs": kwargs,
                      "line": d.start_point.row + 1})  # fmt: skip
    return found


def _params(params: TsNode | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    keyword_only = False
    for p in params.named_children if params is not None else []:
        kind = "keyword" if keyword_only else "positional"
        name, type_, default = "", "", None
        match p.type:
            case "identifier":
                name = text(p)
            case "typed_parameter":
                inner = p.named_children[0]
                if inner.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                    kind = "var"
                    inner = inner.named_children[0] if inner.named_children else inner
                name, type_ = text(inner), compact(text(p.child_by_field_name("type")))
            case "default_parameter" | "typed_default_parameter":
                name = text(p.child_by_field_name("name"))
                type_ = compact(text(p.child_by_field_name("type")))
                default = compact(text(p.child_by_field_name("value")))
            case "list_splat_pattern" | "dictionary_splat_pattern":
                name, kind = text(p.named_children[0]) if p.named_children else "", "var"
            case "keyword_separator" | "list_splat":
                keyword_only = True
                continue
            case _:
                continue
        if p.type == "list_splat_pattern":
            keyword_only = True
        entry: dict[str, Any] = {"name": name, "type": type_, "annotations": [], "kind": kind}
        if default is not None:
            entry["default"] = default
        out.append(entry)
    return out


class _File:
    def __init__(self, rel: str, root: TsNode, out: Extraction, seen: set[str],
                 sites: list[PyCallSite], scopes: dict[str, dict[str, str]]) -> None:  # fmt: skip
        self.rel, self.out, self.seen, self.sites, self.scopes = rel, out, seen, sites, scopes
        self.module = module_name(rel)
        package = self.module.rsplit(".", 1)[0] if "." in self.module else ""
        if rel.endswith("__init__.py"):
            package = self.module
        self.package = package
        self.aliases: dict[str, str] = {}  # local name -> dotted target
        self.import_lines: list[str] = []
        self.apps: list[dict[str, Any]] = []
        self.wiring: list[dict[str, Any]] = []
        self._scan(root)
        self.module_id = self._unique(f"python:class:{self.module}")
        end = max(root.end_point.row + (0 if root.end_point.column == 0 else 1), 1)
        name = self.module.rsplit(".", 1)[-1]
        attributes: dict[str, Any] = {
            "qualified_name": self.module, "type_kind": "module", "package": package,
            "modifiers": [], "annotations": [], "extends": [], "implements": [],
            "imports": self.import_lines, "aliases": self.aliases,
            "apps": self.apps, "wiring": self.wiring,
        }  # fmt: skip
        if docstring := self._docstring(root):
            attributes["docstring"] = docstring
        self._add(Node(self.module_id, NodeKind.CLASS, name, rel, 1, end, "python", attributes),
                  None, 1)  # fmt: skip
        self.scopes[self.module_id] = {}
        self._block(root, self.module_id, self.module, in_class=False)

    # ---- whole-file scans: imports, app objects, router wiring -----------------

    def _resolve_relative(self, raw: str) -> str:
        dots = len(raw) - len(raw.lstrip("."))
        if not dots:
            return raw
        base = self.package.split(".") if self.package else []
        base = base[: len(base) - (dots - 1)] if dots > 1 else base
        rest = raw[dots:]
        return ".".join([*base, rest] if rest else base)

    def _scan(self, root: TsNode) -> None:
        def visit(n: TsNode) -> None:
            if n.type == "import_statement":
                self.import_lines.append(compact(text(n)))
                for item in n.named_children:
                    if item.type == "aliased_import":
                        target = text(item.child_by_field_name("name"))
                        self.aliases[text(item.child_by_field_name("alias"))] = target
                    elif item.type == "dotted_name":
                        head = text(item).split(".")[0]
                        self.aliases.setdefault(head, head)
                return
            if n.type == "import_from_statement":
                self.import_lines.append(compact(text(n)))
                module = self._resolve_relative(text(n.child_by_field_name("module_name")))
                for item in n.children_by_field_name("name"):
                    if item.type == "aliased_import":
                        target = text(item.child_by_field_name("name"))
                        local = text(item.child_by_field_name("alias"))
                    else:
                        target = local = text(item)
                    self.aliases[local] = f"{module}.{target}" if module else target
                return
            if n.type == "assignment":
                right = n.child_by_field_name("right")
                left = n.child_by_field_name("left")
                if right is not None and right.type == "call" and left is not None:
                    callee = compact(text(right.child_by_field_name("function")))
                    if callee.rsplit(".", 1)[-1] in _APP_CALLS:
                        args, kwargs = call_args(right)
                        self.apps.append({"var": text(left), "callee": callee, "args": args,
                                          "kwargs": kwargs,
                                          "line": n.start_point.row + 1})  # fmt: skip
            if n.type == "call":
                function = n.child_by_field_name("function")
                if function is not None and function.type == "attribute":
                    method = text(function.child_by_field_name("attribute"))
                    if method in _WIRING:
                        args, kwargs = call_args(n)
                        self.wiring.append({
                            "target": compact(text(function.child_by_field_name("object"))),
                            "method": method, "args": args, "kwargs": kwargs,
                            "line": n.start_point.row + 1})  # fmt: skip
            for child in n.named_children:
                visit(child)

        visit(root)

    @staticmethod
    def _docstring(block: TsNode | None) -> str | None:
        first = block.named_children[0] if block is not None and block.named_children else None
        if first is not None and first.type == "expression_statement" and first.named_children:
            value = string(first.named_children[0])
            return value.strip()[:500] if value else None
        return None

    # ---- nodes ----------------------------------------------------------------

    def _unique(self, node_id: str, line: int | None = None) -> str:
        # Same name twice (a property setter, two modules with one dotted name): keep both.
        if node_id in self.seen:
            node_id = f"{node_id}@{self.rel}" + (f":{line}" if line else "")
        self.seen.add(node_id)
        return node_id

    def _add(self, node: Node, parent_id: str | None, line: int) -> None:
        self.out.nodes.append(node)
        if parent_id is not None:
            self.out.edges.append(Edge(f"contains:{parent_id}>{node.id}", parent_id, node.id,
                                       EdgeKind.CONTAINS, Confidence.EXACT, self.rel, line,
                                       _RESOLVER))  # fmt: skip

    def _block(self, block: TsNode, owner_id: str, owner: str, *, in_class: bool) -> None:
        for stmt in block.named_children:
            decorators: list[dict[str, Any]] = []
            target = stmt
            if stmt.type == "decorated_definition":
                decorators = _decorators(stmt)
                target = stmt.child_by_field_name("definition") or stmt
            if target.type == "class_definition":
                self._class(target, stmt, decorators, owner_id, owner)
            elif target.type == "function_definition":
                self._function(target, stmt, decorators, owner_id, owner, in_class=in_class)
            elif stmt.type == "expression_statement" and stmt.named_children:
                expr = stmt.named_children[0]
                if expr.type == "assignment":
                    self._assignment(expr, owner_id, owner)

    def _class(self, decl: TsNode, outer: TsNode, decorators: list[dict[str, Any]],
               parent_id: str, scope: str) -> None:  # fmt: skip
        name = text(decl.child_by_field_name("name"))
        qualified = f"{scope}.{name}"
        class_id = self._unique(f"python:class:{qualified}", decl.start_point.row + 1)
        bases, kwargs = [], {}
        superclasses = decl.child_by_field_name("superclasses")
        for arg in superclasses.named_children if superclasses is not None else []:
            if arg.type == "keyword_argument":
                kwargs[text(arg.child_by_field_name("name"))] = compact(
                    text(arg.child_by_field_name("value"))
                )
            elif arg.type != "comment":
                bases.append(compact(text(arg)))
        attributes: dict[str, Any] = {
            "qualified_name": qualified, "type_kind": "class", "package": self.module,
            "modifiers": [], "annotations": [d["name"] for d in decorators],
            "decorators": decorators, "extends": bases, "implements": [],
            "class_kwargs": kwargs,
        }  # fmt: skip
        body = decl.child_by_field_name("body")
        if docstring := self._docstring(body):
            attributes["docstring"] = docstring
        start, end = outer.start_point.row + 1, outer.end_point.row + 1
        self._add(Node(class_id, NodeKind.CLASS, name, self.rel, start, end, "python",
                       attributes), parent_id, start)  # fmt: skip
        self.scopes[class_id] = {}
        if body is not None:
            self._block(body, class_id, qualified, in_class=True)

    def _function(self, decl: TsNode, outer: TsNode, decorators: list[dict[str, Any]],
                  owner_id: str, owner: str, *, in_class: bool) -> None:  # fmt: skip
        name = text(decl.child_by_field_name("name"))
        params = _params(decl.child_by_field_name("parameters"))
        names = [d["name"] for d in decorators]
        bound = in_class and "staticmethod" not in names and params
        if bound:
            params = params[1:]  # self / cls
        signature = ",".join(p["name"] for p in params)
        start, end = outer.start_point.row + 1, outer.end_point.row + 1
        method_id = self._unique(f"python:method:{owner}#{name}({signature})", start)
        modifiers = ["async"] if text(decl).startswith("async") else []
        if "staticmethod" in names:
            modifiers.append("static")
        if "classmethod" in names:
            modifiers.append("classmethod")
        returns = decl.child_by_field_name("return_type")
        attributes: dict[str, Any] = {
            "return_type": compact(text(returns)) if returns is not None else None,
            "parameters": params, "modifiers": modifiers, "annotations": names,
            "decorators": decorators, "constructor": name == "__init__",
        }  # fmt: skip
        body = decl.child_by_field_name("body")
        if docstring := self._docstring(body):
            attributes["docstring"] = docstring
        self._add(Node(method_id, NodeKind.METHOD, name, self.rel, start, end, "python",
                       attributes), owner_id, start)  # fmt: skip
        scope = {p["name"]: p["type"] for p in params if p["type"]}
        if bound:  # self (an instance of the class) or cls (the class itself)
            first = _params(decl.child_by_field_name("parameters"))[0]["name"]
            scope[first] = ("\x01n" if "classmethod" in names else "\x01i") + owner_id
        self.scopes[method_id] = scope
        if body is None:
            return
        self._calls(body, method_id, owner_id, scope, in_class=in_class)
        if in_class and name == "__init__":
            self._self_fields(body, owner_id, owner)
        flow = extract_flow(body)
        if flow["branches"] or decorators:
            attributes["flow"] = flow

    def _assignment(self, expr: TsNode, owner_id: str, owner: str) -> None:
        left = expr.child_by_field_name("left")
        if left is None or left.type != "identifier":
            return
        right = expr.child_by_field_name("right")
        type_ = compact(text(expr.child_by_field_name("type")))
        self._field(owner_id, owner, text(left), type_, right, expr)

    def _field(self, owner_id: str, owner: str, name: str, type_: str, value: TsNode | None,
               decl: TsNode) -> None:  # fmt: skip
        field_id = f"python:field:{owner}#{name}"
        if field_id in self.seen:
            return  # reassigned: the first assignment defines it
        self.seen.add(field_id)
        attributes: dict[str, Any] = {"type": type_, "modifiers": [], "annotations": []}
        if value is not None:
            attributes["value"] = compact(text(value))[:_MAX_VALUE]
            if value.type == "call":
                attributes["call"] = compact(text(value.child_by_field_name("function")))
                attributes["call_args"], attributes["call_kwargs"] = call_args(value)
            elif (literal := string(value)) is not None:
                attributes["literal"] = literal
        start, end = decl.start_point.row + 1, decl.end_point.row + 1
        self._add(Node(field_id, NodeKind.FIELD, name, self.rel, start, end, "python",
                       attributes), owner_id, start)  # fmt: skip

    def _self_fields(self, body: TsNode, class_id: str, owner: str) -> None:
        def visit(n: TsNode) -> None:
            if n.type in ("function_definition", "lambda", "class_definition"):
                return
            if n.type == "assignment":
                left = n.child_by_field_name("left")
                if (left is not None and left.type == "attribute"
                        and text(left.child_by_field_name("object")) == "self"):  # fmt: skip
                    name = text(left.child_by_field_name("attribute"))
                    type_ = compact(text(n.child_by_field_name("type")))
                    self._field(class_id, owner, name, type_, n.child_by_field_name("right"), n)
            for child in n.named_children:
                visit(child)

        visit(body)

    def _calls(self, body: TsNode, method_id: str, owner_id: str, scope: dict[str, str], *,
               in_class: bool) -> None:  # fmt: skip
        def visit(n: TsNode) -> None:
            if n.type == "class_definition":
                return
            for child in n.named_children:
                visit(child)
            if n.type == "assignment":
                left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
                type_ = compact(text(n.child_by_field_name("type")))
                if left is not None and left.type == "identifier":
                    if type_:
                        scope[text(left)] = type_
                    elif right is not None and (made := chain(right)) is not None and \
                            made.endswith(")"):  # fmt: skip
                        scope.setdefault(text(left), "()" + made)
            elif n.type == "as_pattern":  # with x() as y
                alias = n.child_by_field_name("alias")
                names = [c for c in alias.named_children if c.type == "identifier"] if \
                    alias is not None else []  # fmt: skip
                made = chain(n.named_children[0]) if n.named_children else None
                if len(names) == 1 and made is not None and made.endswith(")"):
                    scope.setdefault(text(names[0]), "()" + made)
            elif n.type == "call":
                function = n.child_by_field_name("function")
                line = n.start_point.row + 1
                if function is None:
                    return
                if function.type == "identifier":
                    self.sites.append(PyCallSite(method_id, owner_id, self.rel, line,
                                                 text(function), None, in_class))  # fmt: skip
                elif function.type == "attribute":
                    obj = function.child_by_field_name("object")
                    receiver = "super()" if text(obj) == "super()" else chain(obj)
                    self.sites.append(PyCallSite(method_id, owner_id, self.rel, line,
                                                 text(function.child_by_field_name("attribute")),
                                                 receiver if receiver is not None else "<expr>",
                                                 in_class))  # fmt: skip

        visit(body)


class PythonAdapter:
    language: str = "python"
    extensions: tuple[str, ...] = (".py",)

    def __init__(self) -> None:
        self._parser = Parser(_LANGUAGE)

    def extract(self, root: Path, files: Sequence[str]) -> Extraction:
        out = Extraction()
        seen: set[str] = set()
        sites: list[PyCallSite] = []
        scopes: dict[str, dict[str, str]] = {}
        for rel in files:
            tree = self._parser.parse((root / rel).read_bytes())
            if tree.root_node.has_error:
                out.files_with_errors.append(rel)
            _File(rel, tree.root_node, out, seen, sites, scopes)
            out.files_parsed += 1
        resolver = PythonResolver(out, sites, scopes)
        resolver.run()
        add_web(out, resolver)
        return out
