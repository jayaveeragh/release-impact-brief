"""Branch structure of one Java method body, from syntax alone.

Recorded per method (attribute `flow`), for request handlers and methods with
branches: each branch point (`if`, `switch`, ternary, `catch`) with its line,
condition text, what the condition reads, the returns/throws inside it and the
branch it is nested in; local variable assignments and what they read; calls
and what their arguments read; and `rejectValue`/`reject` calls (Spring's way of
adding a validation error). A "read" is a variable (`page`), a zero-argument
call on one (`order.getCode()`), or a call with arguments on one
(`owner.getPet(...)`, whose arguments are read separately). Names that start
with a capital letter are treated as classes (`Math.max`), not inputs. Lambda
bodies are skipped: what they read happens later, if ever.
Resolving reads to request inputs happens later (branches.py), on demand.
"""

from typing import Any

from tree_sitter import Node as TsNode

_BRANCHES = {"if_statement", "switch_expression", "switch_statement", "ternary_expression",
             "catch_clause"}  # fmt: skip
_REJECTS = {"rejectValue", "reject"}


def _text(node: TsNode | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text else ""


def _line(node: TsNode) -> int:
    return node.start_point.row + 1


def _is_class_name(name: str) -> bool:
    return name[:1].isupper()


def reads(node: TsNode | None) -> list[str]:
    """What an expression reads, in source order, without duplicates."""
    found: list[str] = []

    def add(ref: str) -> None:
        if ref not in found:
            found.append(ref)

    def walk(n: TsNode) -> None:
        match n.type:
            case "lambda_expression" | "string_literal" | "class_literal":
                return
            case "identifier":
                if not _is_class_name(_text(n)):
                    add(_text(n))
                return
            case "method_invocation":
                obj = n.child_by_field_name("object")
                args = n.child_by_field_name("arguments")
                name = _text(n.child_by_field_name("name"))
                has_args = args is not None and len(args.named_children) > 0
                if obj is not None and obj.type == "identifier" and not _is_class_name(_text(obj)):
                    add(f"{_text(obj)}.{name}({'...' if has_args else ''})")
                elif obj is not None and obj.type == "this":
                    pass  # this.m(): a call on the class itself
                elif obj is not None:
                    walk(obj)  # chained call or class name: read what the object reads
                if args is not None:
                    walk(args)
                return
            case "field_access":
                obj = n.child_by_field_name("object")
                field = _text(n.child_by_field_name("field"))
                if obj is not None and obj.type == "this":
                    add(f"this.{field}")
                elif (
                    obj is not None and obj.type == "identifier" and not _is_class_name(_text(obj))
                ):
                    add(f"{_text(obj)}.{field}")
                elif obj is not None:
                    walk(obj)
                return
        for child in n.named_children:
            walk(child)

    if node is not None:
        walk(node)
    return found


def _exits(node: TsNode | None) -> list[int]:
    """Lines of return/throw statements anywhere inside node (not in lambdas)."""
    found: list[int] = []

    def walk(n: TsNode) -> None:
        if n.type == "lambda_expression":
            return
        if n.type in ("return_statement", "throw_statement"):
            found.append(_line(n))
        for child in n.named_children:
            walk(child)

    if node is not None:
        walk(node)
    return sorted(found)


def _cases(switch: TsNode) -> list[dict[str, Any]]:
    body = switch.child_by_field_name("body")
    cases = []
    for group in body.named_children if body is not None else []:
        if group.type not in ("switch_rule", "switch_block_statement_group"):
            continue
        labels = [c for c in group.named_children if c.type == "switch_label"]
        names = []
        for label in labels:
            values = [_text(v) for v in label.named_children]
            names.append(", ".join(values) if values else "default")
        cases.append({"label": " / ".join(names), "line": _line(group), "exits": _exits(group)})
    return cases


def extract_flow(body: TsNode) -> dict[str, Any]:
    flow: dict[str, Any] = {"branches": [], "locals": [], "calls": [], "rejects": [],
                            "returns": [], "throws": []}  # fmt: skip
    stack: list[int] = []  # indexes of the branches we are inside

    def walk(n: TsNode) -> None:
        if n.type == "lambda_expression":
            return
        pushed = False
        if n.type in _BRANCHES:
            branch: dict[str, Any] = {"kind": n.type.split("_")[0], "line": _line(n),
                                      "end_line": n.end_point.row + 1,
                                      "parent": stack[-1] if stack else None}  # fmt: skip
            if n.type == "catch_clause":
                param = next((c for c in n.named_children if c.type == "catch_formal_parameter"),
                             None)  # fmt: skip
                catch_type = next((c for c in param.named_children if c.type == "catch_type"),
                                  None) if param is not None else None  # fmt: skip
                branch.update(condition="", reads=[], exception=_text(catch_type),
                              exits=_exits(n.child_by_field_name("body")))  # fmt: skip
            else:
                condition = n.child_by_field_name("condition")
                if condition is not None and condition.type == "parenthesized_expression":
                    condition = condition.named_children[0] if condition.named_children else None
                branch.update(condition=" ".join(_text(condition).split()), reads=reads(condition))
                if n.type in ("switch_expression", "switch_statement"):
                    branch.update(kind="switch", cases=_cases(n), exits=_exits(n))
                else:
                    branch["exits"] = _exits(n.child_by_field_name("consequence"))
                    if n.type == "if_statement":
                        branch["else_exits"] = _exits(n.child_by_field_name("alternative"))
            flow["branches"].append(branch)
            stack.append(len(flow["branches"]) - 1)
            pushed = True
        elif n.type == "local_variable_declaration":
            for d in n.children_by_field_name("declarator"):
                value = d.child_by_field_name("value")
                if value is not None:
                    flow["locals"].append({"name": _text(d.child_by_field_name("name")),
                                           "line": _line(d), "reads": reads(value)})  # fmt: skip
        elif n.type == "assignment_expression":
            left = n.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                flow["locals"].append({"name": _text(left), "line": _line(n),
                                       "reads": reads(n.child_by_field_name("right"))})  # fmt: skip
        elif n.type == "method_invocation":
            obj = n.child_by_field_name("object")
            name = _text(n.child_by_field_name("name"))
            callee = f"{_text(obj)}.{name}" if obj is not None else name
            if name in _REJECTS and obj is not None and obj.type == "identifier":
                flow["rejects"].append({"line": _line(n), "target": _text(obj),
                                        "branch": stack[-1] if stack else None})  # fmt: skip
            else:
                passed = reads(n.child_by_field_name("arguments"))
                flow["calls"].append({"line": _line(n), "callee": callee, "reads": passed})
        elif n.type == "return_statement":
            flow["returns"].append(_line(n))
        elif n.type == "throw_statement":
            flow["throws"].append(_line(n))
        for child in n.named_children:
            walk(child)
        if pushed:
            stack.pop()

    walk(body)
    return flow
