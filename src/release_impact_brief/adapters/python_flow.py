"""Branch structure of one Python function body, in the same `flow` shape as Java's.

Branch points: `if` / `elif` (an elif is an `if` nested in the one before it, as a Java
`else if` is), `match` (with its cases), conditional expressions (`a if c else b`, kind
"ternary") and `except` clauses (kind "catch"). Loops are not branch points, as for Java.
A "read" is a name (`page`), an attribute of one (`user.email`, `self.x`), or a call on
one (`form.validate_on_submit()`; with arguments `user.is_following(...)`). Names that
start with a capital letter are classes. Lambdas and nested functions are skipped.

Flask request data is read straight from the global `request`, so those reads are
recorded as request refs instead: `request.args.get("page", 1, type=int)` ->
`request:query:page`, `request.get_json()` -> `request:body`; each also goes in
`requests` with its source, name, type and default, for the endpoint's inputs.
"""

from typing import Any

from tree_sitter import Node as TsNode

from release_impact_brief.adapters.python_text import compact, kwarg, string, text

_SKIP = {"lambda", "function_definition", "class_definition"}
_SOURCES = {"args": "query", "form": "form", "values": "query", "headers": "header",
            "cookies": "cookie", "files": "multipart", "view_args": "path"}  # fmt: skip
_BODY = {"json", "data", "get_json", "get_data"}
_CONSTANTS = {"None", "True", "False", "self", "cls"}


def _line(node: TsNode) -> int:
    return node.start_point.row + 1


def _request_ref(node: TsNode) -> dict[str, Any] | None:
    """`request.args.get("x", 1, type=int)`, `request.form["x"]`, `request.get_json()`..."""
    call_args = None
    target = node
    if node.type == "call":
        call_args = node.child_by_field_name("arguments")
        target = node.child_by_field_name("function") or node
    elif node.type == "subscript":
        target = node.child_by_field_name("value") or node
    if target.type != "attribute":
        return None
    obj = target.child_by_field_name("object")
    attr = text(target.child_by_field_name("attribute"))
    if obj is not None and text(obj) == "request" and attr in _BODY:
        return {"ref": "request:body", "source": "body", "name": "body"}
    if obj is None or obj.type != "attribute":
        return None
    root, part = obj.child_by_field_name("object"), text(obj.child_by_field_name("attribute"))
    if text(root) != "request" or part not in _SOURCES:
        return None
    if node.type == "subscript":
        key = string(node.child_by_field_name("subscript"))
    elif attr in ("get", "getlist") and call_args is not None and call_args.named_children:
        key = string(call_args.named_children[0])
    else:
        return None
    if key is None:
        return None
    entry: dict[str, Any] = {"ref": f"request:{_SOURCES[part]}:{key}", "source": _SOURCES[part],
                             "name": key}  # fmt: skip
    if call_args is not None:
        positional = [a for a in call_args.named_children if a.type != "keyword_argument"]
        if len(positional) > 1:
            entry["default"] = compact(text(positional[1]))
        if (type_ := kwarg(node, "type")) is not None:
            entry["type"] = type_
        if (default := kwarg(node, "default")) is not None:
            entry["default"] = default
    if node.type == "subscript":
        entry["required"] = True
    return entry


def reads(node: TsNode | None, requests: list[dict[str, Any]] | None = None) -> list[str]:
    """What an expression reads, in source order, without duplicates."""
    found: list[str] = []

    def add(ref: str) -> None:
        if ref not in found:
            found.append(ref)

    def walk(n: TsNode) -> None:
        if n.type in _SKIP or n.type in ("string", "integer", "float", "comment"):
            return
        if (req := _request_ref(n)) is not None:
            add(req["ref"])
            if requests is not None and req not in requests:
                requests.append(req)
            return
        match n.type:
            case "identifier":
                name = text(n)
                if not name[:1].isupper() and name not in _CONSTANTS:
                    add(name)
                return
            case "attribute":
                obj = n.child_by_field_name("object")
                attr = text(n.child_by_field_name("attribute"))
                if obj is not None and obj.type == "identifier":
                    head = text(obj)
                    if head in ("self", "cls"):
                        add(f"self.{attr}")
                    elif not head[:1].isupper():
                        add(f"{head}.{attr}")
                    return
                if obj is not None:
                    walk(obj)
                return
            case "call":
                function = n.child_by_field_name("function")
                args = n.child_by_field_name("arguments")
                has_args = args is not None and len(args.named_children) > 0
                if function is not None and function.type == "attribute":
                    obj = function.child_by_field_name("object")
                    name = text(function.child_by_field_name("attribute"))
                    plain = obj is not None and obj.type == "identifier" and \
                        not text(obj)[:1].isupper() and text(obj) not in _CONSTANTS  # fmt: skip
                    if plain:
                        add(f"{text(obj)}.{name}({'...' if has_args else ''})")
                    elif obj is not None and text(obj) not in ("self", "cls"):
                        walk(obj)
                if args is not None:
                    walk(args)
                return
            case "keyword_argument":
                value = n.child_by_field_name("value")
                if value is not None:
                    walk(value)
                return
        for child in n.named_children:
            walk(child)

    if node is not None:
        walk(node)
    return found


def _exits(node: TsNode | None) -> list[int]:
    """Lines of return/raise statements anywhere inside node (not in nested functions)."""
    found: list[int] = []

    def walk(n: TsNode) -> None:
        if n.type in _SKIP:
            return
        if n.type in ("return_statement", "raise_statement"):
            found.append(_line(n))
        for child in n.named_children:
            walk(child)

    if node is not None:
        walk(node)
    return sorted(found)


def extract_flow(body: TsNode) -> dict[str, Any]:
    flow: dict[str, Any] = {"branches": [], "locals": [], "calls": [], "rejects": [],
                            "returns": [], "throws": [], "requests": []}  # fmt: skip
    requests: list[dict[str, Any]] = flow["requests"]
    stack: list[int] = []

    def branch(kind: str, n: TsNode, condition: TsNode | None, **extra: Any) -> None:
        entry: dict[str, Any] = {"kind": kind, "line": _line(n), "end_line": n.end_point.row + 1,
                                 "parent": stack[-1] if stack else None,
                                 "condition": compact(text(condition)),
                                 "reads": reads(condition, requests)}  # fmt: skip
        entry.update(extra)
        flow["branches"].append(entry)

    def walk(n: TsNode) -> None:
        if n.type in _SKIP:
            return
        pushed, before = 0, len(flow["branches"])
        if n.type in ("if_statement", "elif_clause"):
            alternatives = n.children_by_field_name("alternative") if n.type == "if_statement" \
                else []  # fmt: skip
            else_exits = sorted(x for a in alternatives for x in _exits(a))
            branch(
                "if",
                n,
                n.child_by_field_name("condition"),
                exits=_exits(n.child_by_field_name("consequence")),
                else_exits=else_exits,
            )
            if n.type == "if_statement":
                end = n.child_by_field_name("consequence")
                flow["branches"][-1]["end_line"] = (end or n).end_point.row + 1
        elif n.type == "match_statement":
            body = n.child_by_field_name("body")
            cases = [{"label": compact(text(c.named_children[0])) if c.named_children else "",
                      "line": _line(c), "exits": _exits(c)}
                     for c in (body.named_children if body is not None else [])
                     if c.type == "case_clause"]  # fmt: skip
            branch("switch", n, n.child_by_field_name("subject"), cases=cases, exits=_exits(n))
        elif n.type == "conditional_expression" and len(n.named_children) == 3:
            branch("ternary", n, n.named_children[1], exits=[])
        elif n.type == "except_clause":
            types = [c for c in n.named_children if c.type != "block"]
            block = next((c for c in n.named_children if c.type == "block"), None)
            branch(
                "catch",
                n,
                None,
                exception=", ".join(compact(text(t)) for t in types),
                exits=_exits(block),
            )
        elif n.type == "assignment" or n.type == "augmented_assignment":
            left = n.child_by_field_name("left")
            names = [left] if left is not None and left.type == "identifier" else [
                c for c in (left.named_children if left is not None else [])
                if c.type == "identifier"]  # fmt: skip
            value = reads(n.child_by_field_name("right"), requests)
            for target in names:
                flow["locals"].append({"name": text(target), "line": _line(n), "reads": value})
        elif n.type == "call":
            function = n.child_by_field_name("function")
            callee = compact(text(function))[:80]
            if _request_ref(n) is None:
                flow["calls"].append({"line": _line(n), "callee": callee,
                                      "reads": reads(n.child_by_field_name("arguments"),
                                                     requests)})  # fmt: skip
        elif n.type == "return_statement":
            flow["returns"].append(_line(n))
        elif n.type == "raise_statement":
            flow["throws"].append(_line(n))
        if len(flow["branches"]) > before:
            stack.append(len(flow["branches"]) - 1)
            pushed = 1
        for child in n.named_children:
            walk(child)
        if pushed:
            stack.pop()

    walk(body)
    return flow
