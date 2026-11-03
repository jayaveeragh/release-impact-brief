"""Spring MVC / WebFlux endpoints from annotations (syntax only).

An endpoint node is created per HTTP method + full path, linked to its handler
method by a `handles` edge. Paths built from constants we can't evaluate keep
the raw expression and are marked `guess`, so they're visible but not trusted.
"""

from typing import Any

from release_impact_brief.adapters import Extraction
from release_impact_brief.facts import Confidence, Edge, Node, NodeKind

_RESOLVER = "java-spring"
_CONTROLLERS = {"Controller", "RestController"}
_VERB_MAPPINGS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}
_SOURCES = {
    "PathVariable": "path",
    "RequestParam": "query",
    "RequestBody": "body",
    "RequestHeader": "header",
    "CookieValue": "cookie",
    "ModelAttribute": "form",
    "RequestPart": "multipart",
}
# Types Spring supplies to a handler itself; they are not inputs from the caller.
_FRAMEWORK_TYPES = {
    "Model",
    "ModelMap",
    "Map",
    "BindingResult",
    "Errors",
    "RedirectAttributes",
    "SessionStatus",
    "HttpServletRequest",
    "HttpServletResponse",
    "ServerHttpRequest",
    "ServerHttpResponse",
    "ServerWebExchange",
    "HttpSession",
    "Principal",
    "Authentication",
    "Locale",
    "WebRequest",
    "UriComponentsBuilder",
    "Pageable",
}
_SIMPLE_TYPES = {
    "String",
    "int",
    "Integer",
    "long",
    "Long",
    "short",
    "Short",
    "double",
    "Double",
    "float",
    "Float",
    "boolean",
    "Boolean",
    "char",
    "Character",
    "byte",
    "Byte",
    "BigDecimal",
    "BigInteger",
    "LocalDate",
    "LocalDateTime",
    "UUID",
}


def _literal(raw: str) -> str | None:
    """The string value if raw is a plain string literal, else None."""
    if len(raw) >= 2 and raw[0] == raw[-1] == '"' and '"' not in raw[1:-1]:
        return raw[1:-1]
    return None


def _paths(args: dict[str, list[str]]) -> tuple[list[str], bool]:
    """Mapping paths and whether all of them are literals."""
    raw = args.get("value") or args.get("path") or []
    if not raw:
        return [""], True
    values = [_literal(r) for r in raw]
    return [v if v is not None else f"<{r}>" for v, r in zip(values, raw, strict=True)], all(
        v is not None for v in values
    )


def _join(prefix: str, path: str) -> str:
    joined = "/" + "/".join(part for part in f"{prefix}/{path}".split("/") if part)
    return joined


def _mappings(annotation_args: dict[str, dict[str, list[str]]], names: list[str]) -> list[
    tuple[str, list[str], bool]
]:  # fmt: skip
    """(verb, paths, literal) for each mapping annotation on a method."""
    found = []
    for name in names:
        args = annotation_args.get(name, {})
        if name in _VERB_MAPPINGS:
            paths, literal = _paths(args)
            found.append((_VERB_MAPPINGS[name], paths, literal))
        elif name == "RequestMapping":
            paths, literal = _paths(args)
            verbs = [m.rsplit(".", 1)[-1] for m in args.get("method", [])] or ["ANY"]
            found += [(v, paths, literal) for v in verbs]
    return found


def _base_type(type_text: str) -> str:
    return type_text.split("<", 1)[0].removesuffix("...")


def _input(param: dict[str, Any]) -> dict[str, Any] | None:
    """Describe where a handler parameter comes from; None for framework-supplied ones."""
    annotations = set(param["annotations"])
    args = param.get("annotation_args", {})
    base = _base_type(param["type"])
    entry: dict[str, Any] = {"name": param["name"], "type": param["type"]}
    source = next((_SOURCES[a] for a in param["annotations"] if a in _SOURCES), None)
    if source is not None:
        own = args.get(next(a for a in param["annotations"] if a in _SOURCES), {})
        names = own.get("value") or own.get("name") or []
        if names and (alias := _literal(names[0])):
            entry["name"] = alias
        if "required" in own:
            entry["required"] = own["required"][0] != "false"
        if "defaultValue" in own:
            entry["default"] = _literal(own["defaultValue"][0]) or own["defaultValue"][0]
    elif base in _FRAMEWORK_TYPES:
        return None
    elif base in _SIMPLE_TYPES:
        source = "query"  # Spring binds a simple unannotated parameter like @RequestParam
        entry["implicit"] = True
    else:
        source = "form"  # ...and a complex one like @ModelAttribute
        entry["implicit"] = True
    entry["source"] = source
    if annotations & {"Valid", "Validated"}:
        entry["validated"] = True
    return entry


def add_endpoints(out: Extraction) -> None:
    nodes = {n.id: n for n in out.nodes}
    methods_of: dict[str, list[Node]] = {}
    for e in out.edges:
        if e.kind == "contains" and nodes[e.dst].kind == NodeKind.METHOD:
            methods_of.setdefault(e.src, []).append(nodes[e.dst])
    seen: set[str] = set()
    for cls in [n for n in out.nodes if n.kind == NodeKind.CLASS]:
        class_annotations = set(cls.attributes["annotations"])
        if not class_annotations & _CONTROLLERS:
            continue
        class_args = cls.attributes.get("annotation_args", {})
        prefixes, prefix_literal = _paths(class_args.get("RequestMapping", {}))
        rest = "RestController" in class_annotations
        for method in methods_of.get(cls.id, []):
            attrs = method.attributes
            for verb, paths, literal in _mappings(
                attrs.get("annotation_args", {}), attrs["annotations"]
            ):
                for prefix in prefixes:
                    for path in paths:
                        full = _join(prefix, path)
                        endpoint_id = f"java:endpoint:{verb} {full}"
                        if endpoint_id in seen:
                            endpoint_id += f"@{method.id.removeprefix('java:method:')}"
                        seen.add(endpoint_id)
                        returns = attrs["return_type"] or "void"
                        body = (
                            rest
                            or "ResponseBody" in attrs["annotations"]
                            or _base_type(returns) in ("ResponseEntity", "Mono", "Flux")
                        )
                        inputs = [i for p in attrs["parameters"] if (i := _input(p))]
                        conf = Confidence.EXACT if literal and prefix_literal else Confidence.GUESS
                        out.nodes.append(
                            Node(
                                endpoint_id,
                                NodeKind.ENDPOINT,
                                f"{verb} {full}",
                                method.file,
                                method.start_line,
                                method.end_line,
                                "java",
                                {
                                    "http_method": verb,
                                    "path": full,
                                    "path_resolved": literal and prefix_literal,
                                    "handler": method.id,
                                    "returns": returns,
                                    "response": "body" if body else "view",
                                    "inputs": inputs,
                                },
                            )
                        )
                        out.edges.append(
                            Edge(
                                f"handles:{endpoint_id}>{method.id}",
                                endpoint_id,
                                method.id,
                                "handles",
                                conf,
                                method.file,
                                method.start_line,
                                _RESOLVER,
                            )
                        )
