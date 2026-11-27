"""FastAPI and Flask endpoints, FastAPI dependencies, and SQLModel / SQLAlchemy models.

Endpoints: a function decorated with `<router>.get(...)` (FastAPI, Flask 2) or
`<router>.route(...)` / `<router>.api_route(...)`, where `<router>` is a FastAPI app,
`APIRouter`, Flask app or `Blueprint`. The full path joins the route to its router's own
prefix and to every `include_router(..., prefix=)` / `register_blueprint(...,
url_prefix=)` above it. A prefix that is a plain string is exact; one read from a
settings default (`settings.API_V1_STR`, a class attribute with a string default) is
likely, since the environment can override it; anything else keeps the raw expression
and is a guess. A router that is never mounted keeps only its own prefix, also likely.

Inputs (FastAPI): a parameter whose type or default has `Depends(...)` is supplied by
the framework, not the caller, and becomes a `calls` edge to the dependency; `Query`,
`Path`, `Body`, ... name the source; a name in the path is a path input; a class (a
Pydantic model) is the body, validated; anything else is a query input. Flask: path
inputs from the rule (`<int:id>`), and whatever the handler reads from `request`
(python_flow.py); a WTForms form created in the handler is a validated form input.

Models: SQLModel classes with `table=True`; SQLAlchemy / Flask-SQLAlchemy classes that
derive from a declarative base and have a `__tablename__` or columns. Relations from
`relationship(...)` / `Relationship(...)`, target from the first string argument or
the type hint.
"""

import re
from typing import Any

from release_impact_brief.adapters import Extraction
from release_impact_brief.adapters.python_calls import PythonResolver, Target, base_type, weakest
from release_impact_brief.facts import Confidence, Edge, EdgeKind, Node, NodeKind

_RESOLVER = "python-web"
_VERBS = {"get", "post", "put", "delete", "patch", "options", "head"}
_ROUTER_KINDS = {"FastAPI": ("fastapi", "app"), "APIRouter": ("fastapi", "router"),
                 "Flask": ("flask", "app"), "Blueprint": ("flask", "router")}  # fmt: skip
_SOURCES = {"Query": "query", "Path": "path", "Body": "body", "Header": "header",
            "Cookie": "cookie", "Form": "form", "File": "multipart",
            "UploadFile": "multipart"}  # fmt: skip
_FRAMEWORK_TYPES = {"Request", "Response", "BackgroundTasks", "WebSocket", "HTTPConnection",
                    "SecurityScopes", "Session", "AsyncSession"}  # fmt: skip
_SIMPLE = {"str", "int", "float", "bool", "bytes", "UUID", "uuid.UUID", "datetime", "date",
           "Decimal", "EmailStr"}  # fmt: skip
_DEPENDS = re.compile(r"\bDepends\(\s*([\w.]+)")
_FLASK_PARAM = re.compile(r"<(?:(\w+):)?(\w+)>")
_FASTAPI_PARAM = re.compile(r"\{(\w+)(?::\w+)?\}")
_FLASK_TYPES = {"int": "int", "float": "float", "path": "str", "string": "str", "uuid": "UUID"}
_FORM_BASES = ("FlaskForm", "Form")


def _literal(raw: str) -> str | None:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"" and raw[0] not in raw[1:-1]:
        return raw[1:-1]
    return None


def _join(*parts: str) -> str:
    return "/" + "/".join(p for part in parts for p in part.split("/") if p)


def _list(raw: str) -> list[str]:
    return [v for item in raw.strip("[]()").split(",") if (v := _literal(item.strip()))]


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


class _Web:
    def __init__(self, out: Extraction, resolver: PythonResolver) -> None:
        self.out, self.r = out, resolver
        self.nodes = resolver.nodes
        # (module id, variable) -> {framework, kind, prefix, line}
        self.routers: dict[tuple[str, str], dict[str, Any]] = {}
        # child router -> [(parent router, prefix, confidence)]
        self.parents: dict[tuple[str, str], list[tuple[tuple[str, str], str, Confidence]]] = {}
        self.modules = [n for n in out.nodes if n.language == "python" and n.kind == NodeKind.CLASS
                        and n.attributes.get("type_kind") == "module"]  # fmt: skip

    # ---- routers and how they are mounted ------------------------------------

    def find_routers(self) -> None:
        for module in self.modules:
            for app in module.attributes.get("apps", []):
                found = self.r.expr(app["callee"], module.id, {})
                name = (found.ref if found is not None else app["callee"]).rsplit(".", 1)[-1]
                if found is None or found.kind != "library" or name not in _ROUTER_KINDS:
                    continue
                framework, kind = _ROUTER_KINDS[name]
                prefix = app["kwargs"].get("prefix" if framework == "fastapi" else "url_prefix")
                own, conf = self.value(prefix, module.id) if prefix else ("", Confidence.EXACT)
                self.routers[(module.id, app["var"])] = {
                    "framework": framework, "kind": kind, "prefix": own, "confidence": conf,
                    "file": module.file, "line": app["line"]}  # fmt: skip
                if kind == "app":
                    module.attributes.setdefault("app_framework", framework)

    def value(self, raw: str, module_id: str) -> tuple[str, Confidence]:
        """A prefix's text and how sure it is (see the module docstring)."""
        if (literal := _literal(raw)) is not None:
            return literal, Confidence.EXACT
        field = self.r.field(raw, module_id) if re.fullmatch(r"[\w.]+", raw) else None
        if field is not None:
            node = self.nodes[field]
            if "literal" in node.attributes:
                owner = self.nodes[self.r.parent[node.id]]
                settings = owner.attributes.get("type_kind") == "class"
                return node.attributes["literal"], Confidence.LIKELY if settings else \
                    Confidence.EXACT  # fmt: skip
        return f"<{raw}>", Confidence.GUESS

    def router_of(self, expr: str, module_id: str) -> tuple[str, str] | None:
        if (module_id, expr) in self.routers:
            return (module_id, expr)
        field = self.r.field(expr, module_id) if re.fullmatch(r"[\w.]+", expr) else None
        if field is not None:
            key = (self.r.module_of(field), self.nodes[field].name)
            return key if key in self.routers else None
        return None

    def find_mounts(self) -> None:
        for module in self.modules:
            for w in module.attributes.get("wiring", []):
                parent = self.router_of(w["target"], module.id)
                child = self.router_of(w["args"][0], module.id) if w["args"] else None
                if parent is None or child is None or child == parent:
                    continue
                key = "prefix" if w["method"] == "include_router" else "url_prefix"
                raw = w["kwargs"].get(key)
                prefix, conf = self.value(raw, module.id) if raw else ("", Confidence.EXACT)
                self.parents.setdefault(child, []).append((parent, prefix, conf))

    def prefixes(self, router: tuple[str, str], seen: frozenset[tuple[str, str]] = frozenset()
                 ) -> list[tuple[str, Confidence]]:  # fmt: skip
        """Every full prefix the router is reachable under, with the weakest confidence."""
        info = self.routers[router]
        own, conf = info["prefix"], info["confidence"]
        mounts = [m for m in self.parents.get(router, []) if m[0] not in seen]
        if not mounts:
            mounted = info["kind"] == "app"
            return [(own, conf if mounted else weakest(conf, Confidence.LIKELY))]
        found = []
        for parent, prefix, mount_conf in mounts:
            for above, above_conf in self.prefixes(parent, seen | {router}):
                found.append((_join(above, prefix, own), weakest(conf, mount_conf, above_conf)))
        return found

    # ---- endpoints ---------------------------------------------------------------

    def endpoints(self) -> None:
        seen: set[str] = set()
        decorated = [n for n in self.out.nodes if n.language == "python"
                     and n.kind == NodeKind.METHOD and n.attributes.get("decorators")]  # fmt: skip
        for method in decorated:
            module = self.r.module_of(method.id)
            for d in method.attributes["decorators"]:
                obj, _, verb = d["name"].rpartition(".")
                router = self.router_of(obj, module) if obj else None
                if router is None:
                    continue
                framework = self.routers[router]["framework"]
                raw_path = d["args"][0] if d["args"] else d["kwargs"].get(
                    "path" if framework == "fastapi" else "rule", '""')  # fmt: skip
                if verb in _VERBS:
                    verbs = [verb.upper()]
                elif verb in ("route", "api_route"):
                    verbs = [v.upper() for v in _list(d["kwargs"].get("methods", ""))] or (
                        ["GET"] if framework == "flask" else ["ANY"])  # fmt: skip
                else:
                    continue
                path, path_conf = self.value(raw_path, module)
                for prefix, conf in self.prefixes(router):
                    full = _join(prefix, path)
                    for v in verbs:
                        self.add_endpoint(method, v, full, weakest(conf, path_conf), framework,
                                          d, seen)  # fmt: skip

    def add_endpoint(self, method: Node, verb: str, full: str, conf: Confidence, framework: str,
                     decorator: dict[str, Any], seen: set[str]) -> None:  # fmt: skip
        endpoint_id = f"python:endpoint:{verb} {full}"
        if endpoint_id in seen:
            endpoint_id += f"@{method.id.removeprefix('python:method:')}"
        if endpoint_id in seen:
            return
        seen.add(endpoint_id)
        attrs = method.attributes
        if framework == "fastapi":
            inputs = [i for p in attrs["parameters"] if (i := self.fastapi_input(p, full, method))]
            response = "body"
            returns = decorator["kwargs"].get("response_model") or attrs["return_type"] or "Any"
        else:
            inputs = self.flask_inputs(method, full)
            library = attrs.get("library_calls", [])
            response = "view" if any(c.endswith("render_template") for c in library) else "body"
            returns = attrs["return_type"] or ("template" if response == "view" else "Any")
        self.out.nodes.append(Node(endpoint_id, NodeKind.ENDPOINT, f"{verb} {full}", method.file,
                                   method.start_line, method.end_line, "python", {
                                       "http_method": verb, "path": full,
                                       "path_resolved": conf != Confidence.GUESS,
                                       "handler": method.id, "returns": returns,
                                       "response": response, "inputs": inputs,
                                       "framework": framework,
                                   }))  # fmt: skip
        self.out.edges.append(Edge(f"handles:{endpoint_id}>{method.id}", endpoint_id, method.id,
                                   EdgeKind.HANDLES, conf, method.file, method.start_line,
                                   _RESOLVER))  # fmt: skip
        for dep in _DEPENDS.findall(" ".join(decorator["kwargs"].values())):
            self.depends_edge(method, dep, self.r.module_of(method.id), decorator["line"])

    def fastapi_input(self, p: dict[str, Any], path: str, method: Node) -> dict[str, Any] | None:
        type_, default = p["type"], p.get("default", "")
        module = self.r.module_of(method.id)
        written = f"{type_} {default}"
        alias = self.alias_value(type_, module)
        if re.search(r"\bDepends\(\s*\)", written + alias):
            # `Depends()` on a class: the class reads the request; which parts isn't traced
            return {
                "name": p["name"],
                "type": base_type(alias or type_) or type_,
                "param": p["name"],
                "source": "dependency",
                "required": True,
            }
        if "Depends(" in written or "Depends(" in alias or p["kind"] == "var":
            return None
        base = base_type(alias or type_)
        if base.rsplit(".", 1)[-1] in _FRAMEWORK_TYPES:
            return None
        entry: dict[str, Any] = {"name": p["name"], "type": type_ or "Any", "param": p["name"]}
        explicit = next((s for k, s in _SOURCES.items() if re.search(rf"\b{k}\(", written + alias)),
                        None)  # fmt: skip
        target = self.r.type_target(base, module) if base else None
        if explicit is not None:
            source = explicit
        elif p["name"] in _FASTAPI_PARAM.findall(path):
            source = "path"
        elif target is not None and (
            target.kind == "instance"
            or (
                target.kind == "library"
                and base.rsplit(".", 1)[-1][:1].isupper()
                and base.rsplit(".", 1)[-1] not in _SIMPLE
            )
        ):  # a Pydantic model
            source, entry["validated"] = "body", True
        else:
            source, entry["implicit"] = "query", True
        entry["source"] = source
        if default and "Depends(" not in default:
            if default in ("...", "Query(...)", "Body(...)"):
                entry["required"] = True
            elif (inner := re.match(r"^\w+\((.*)\)$", default)) is not None:
                first = inner.group(1).split(",")[0].strip()
                if first and "=" not in first:
                    entry["default"] = first
            else:
                entry["default"] = default
        else:
            entry["required"] = True
        return entry

    def alias_value(self, type_text: str, module_id: str) -> str:
        """`SessionDep` -> `Annotated[Session, Depends(get_db)]` when it names a type alias."""
        name = type_text.strip()
        if not re.fullmatch(r"[\w.]+", name):
            return ""
        field = self.r.field(name, module_id)
        return str(self.nodes[field].attributes.get("value", "")) if field is not None else ""

    def flask_inputs(self, method: Node, path: str) -> list[dict[str, Any]]:
        inputs: list[dict[str, Any]] = []
        params = {p["name"] for p in method.attributes["parameters"]}
        for converter, name in _FLASK_PARAM.findall(path):
            entry: dict[str, Any] = {"name": name, "type": _FLASK_TYPES.get(converter, "str"),
                                     "source": "path", "required": True}  # fmt: skip
            if name in params:
                entry["param"] = name
            inputs.append(entry)
        flow = method.attributes.get("flow") or {}
        for req in flow.get("requests", []):
            entry = {"name": req["name"], "type": req.get("type", "str" if req["source"] != "body"
                                                           else "JSON"),
                     "source": req["source"], "ref": req["ref"]}  # fmt: skip
            for key in ("default", "required"):
                if key in req:
                    entry[key] = req[key]
            if not any(i.get("ref") == req["ref"] for i in inputs):
                inputs.append(entry)
        module = self.r.module_of(method.id)
        for local in flow.get("locals", []):
            call = next((c for c in flow.get("calls", []) if c["line"] == local["line"]), None)
            if call is None or not call["callee"][:1].isupper():
                continue
            found = self.r.expr(call["callee"], module, {})
            if found is None or found.kind != "node" or \
                    self.nodes[found.ref].kind != NodeKind.CLASS:  # fmt: skip
                continue
            bases = {b.rsplit(".", 1)[-1] for b in self.r.library_bases(found.ref)}
            if bases & set(_FORM_BASES) and \
                    not any(i.get("param") == local["name"] for i in inputs):  # fmt: skip
                inputs.append({"name": local["name"], "type": self.nodes[found.ref].name,
                               "source": "form", "validated": True, "param": local["name"],
                               "class": found.ref})  # fmt: skip
        return inputs

    # ---- FastAPI dependencies --------------------------------------------------------

    def depends_edge(self, method: Node, dep: str, module: str, line: int) -> None:
        found = self.r.expr(dep, module, {})
        if found is None or found.kind != "node" or self.nodes[found.ref].kind != NodeKind.METHOD:
            return
        edge_id = f"calls:{method.id}>{found.ref}"
        if any(e.id == edge_id for e in self.out.edges):
            return
        self.out.edges.append(Edge(edge_id, method.id, found.ref, EdgeKind.CALLS,
                                   weakest(Confidence.EXACT, found.confidence), method.file, line,
                                   _RESOLVER))  # fmt: skip

    def dependencies(self) -> None:
        """`Depends(get_current_user)` (directly or through an `Annotated` alias): FastAPI
        calls the dependency for this function, so it is a call."""
        for method in [n for n in self.out.nodes if n.language == "python"
                       and n.kind == NodeKind.METHOD]:  # fmt: skip
            module = self.r.module_of(method.id)
            for p in method.attributes["parameters"]:
                for dep in _DEPENDS.findall(f"{p['type']} {p.get('default', '')}"):
                    self.depends_edge(method, dep, module, method.start_line)
                alias = self.alias_value(p["type"], module)
                field = self.r.field(p["type"].strip(), module) if alias else None
                if field is not None:
                    alias_module = self.r.module_of(field)
                    for dep in _DEPENDS.findall(alias):
                        self.depends_edge(method, dep, alias_module, method.start_line)

    # ---- models -----------------------------------------------------------------------

    def models(self) -> None:
        classes = [n for n in self.out.nodes if n.language == "python" and n.kind == NodeKind.CLASS
                   and n.attributes.get("type_kind") == "class"]  # fmt: skip
        fields_of: dict[str, dict[str, Node]] = {}
        for c in classes:
            fields_of[c.id] = {
                self.nodes[m].name: self.nodes[m]
                for m in self.r.members.get(c.id, {}).values()
                if self.nodes[m].kind == NodeKind.FIELD
            }
        entity_of: dict[str, str] = {}
        for cls in classes:
            fields = fields_of[cls.id]
            base = " ".join(self.r.library_bases(cls.id)).lower()
            tablename = fields.get("__tablename__")
            if cls.attributes["class_kwargs"].get("table") == "True" and "sqlmodel" in base:
                orm, default = "sqlmodel", cls.name.lower()
            elif "sqlalchemy" in base and "__abstract__" not in fields and (
                tablename is not None or any(
                    (f.attributes.get("call") or "").rsplit(".", 1)[-1] in ("Column",
                                                                           "mapped_column")
                    for f in fields.values())):  # fmt: skip
                orm, default = "sqlalchemy", _snake(cls.name)
            else:
                continue
            literal = tablename.attributes.get("literal") if tablename is not None else None
            table, source = (literal, "__tablename__") if literal else (default, "default")
            cls.attributes["orm"] = orm
            entity_id = f"python:entity:{cls.attributes['qualified_name']}"
            entity_of[cls.id] = entity_id
            self.out.nodes.append(Node(entity_id, NodeKind.ENTITY, table, cls.file,
                                       cls.start_line, cls.end_line, "python",
                                       {"class": cls.id, "table": table,
                                        "table_source": source}))  # fmt: skip
            self.out.edges.append(Edge(f"maps:{cls.id}>{entity_id}", cls.id, entity_id,
                                       EdgeKind.MAPS, Confidence.EXACT, cls.file, cls.start_line,
                                       _RESOLVER))  # fmt: skip
        for class_id in entity_of:
            module = self.r.module_of(class_id)
            for f in fields_of[class_id].values():
                call = (f.attributes.get("call") or "").rsplit(".", 1)[-1]
                if call not in ("relationship", "Relationship"):
                    continue
                first = next(iter(f.attributes.get("call_args", [])), "")
                target_name = _literal(first) or base_type(f.attributes["type"])
                many = bool(re.match(r"^(?:\w+\.)?(?:list|List|WriteOnlyMapped|DynamicMapped)\b|"
                                     r"Mapped\[\s*(?:list|List)\b",
                                     f.attributes["type"]))  # fmt: skip
                f.attributes["relation"] = {"kind": "to_many" if many else "to_one",
                                            "target": target_name}  # fmt: skip
                found = self.r.expr(target_name, module, {}) if target_name else None
                if found is None:  # a string target names a class anywhere in the registry
                    named = [c for c in entity_of if self.nodes[c].name == target_name]
                    found = Target("node", named[0], Confidence.LIKELY) if len(named) == 1 \
                        else None  # fmt: skip
                ref = found.ref if found is not None and found.kind in ("node", "instance") \
                    else None  # fmt: skip
                if ref in entity_of and found is not None:
                    self.out.edges.append(Edge(f"relates:{f.id}>{entity_of[ref]}", f.id,
                                               entity_of[ref], EdgeKind.RELATES,
                                               weakest(Confidence.EXACT, found.confidence),
                                               f.file, f.start_line, _RESOLVER))  # fmt: skip


def add_web(out: Extraction, resolver: PythonResolver) -> None:
    web = _Web(out, resolver)
    web.find_routers()
    web.find_mounts()
    web.endpoints()
    web.dependencies()
    web.models()
