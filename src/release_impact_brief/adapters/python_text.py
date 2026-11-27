"""Small helpers for reading Tree-sitter Python nodes as text."""

from tree_sitter import Node as TsNode


def text(node: TsNode | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text else ""


def compact(value: str) -> str:
    return " ".join(value.split())


def string(node: TsNode | None) -> str | None:
    """The value of a plain string literal (no f-string interpolation), else None."""
    if node is None or node.type != "string":
        return None
    if any(c.type == "interpolation" for c in node.named_children):
        return None
    return "".join(text(c) for c in node.named_children if c.type == "string_content")


def call_args(call: TsNode) -> tuple[list[str], dict[str, str]]:
    """Positional and keyword arguments of a call, as compact source text."""
    args, kwargs = [], {}
    arguments = call.child_by_field_name("arguments")
    for arg in arguments.named_children if arguments is not None else []:
        if arg.type == "keyword_argument":
            kwargs[text(arg.child_by_field_name("name"))] = compact(
                text(arg.child_by_field_name("value"))
            )
        elif arg.type != "comment":
            args.append(compact(text(arg)))
    return args, kwargs


def kwarg(call: TsNode, name: str) -> str | None:
    return call_args(call)[1].get(name)
