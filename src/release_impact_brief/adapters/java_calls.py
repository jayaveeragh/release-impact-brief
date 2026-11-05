"""Cross-file resolution for Java: calls, supertypes, overrides and Spring injection.

Syntax only, no compiler: every edge carries a confidence label saying how sure
the resolution is. Calls into library code (JDK, Spring, ...) are listed on the
calling method as `library_calls` and never traced into; calls that can't be
resolved at all are listed as `unresolved_calls` instead of being guessed.
"""

import re
from dataclasses import dataclass, field

from release_impact_brief.adapters import Extraction
from release_impact_brief.facts import Confidence, Edge, EdgeKind, Node, NodeKind

_RESOLVER = "java-calls"
_RANK = {Confidence.EXACT: 3, Confidence.LIKELY: 2, Confidence.GUESS: 1, Confidence.UNKNOWN: 0}
_PRIMITIVES = {"byte", "short", "int", "long", "float", "double", "boolean", "char", "void", "var"}
_STEREOTYPES = {
    "Component",
    "Service",
    "Repository",
    "Controller",
    "RestController",
    "Configuration",
    "ControllerAdvice",
    "RestControllerAdvice",
}
_INJECT_ANNOTATIONS = {"Autowired", "Inject", "Resource"}
_GENERICS = re.compile(r"<.*>")


@dataclass
class CallSite:
    """One call inside a method body, recorded at parse time and resolved later."""

    method_id: str
    class_id: str
    file: str
    line: int
    name: str  # method name, or the type name for `new`
    arity: int
    kind: str  # "call" | "new"
    receiver: str = "implicit"  # implicit | this | super | name | field | call | new | other
    receiver_text: str = ""
    inner: "CallSite | None" = None


@dataclass
class _Type:
    """What a type expression refers to: a repo class, a library type, or unknown."""

    status: str  # "repo" | "library" | "unknown"
    class_id: str | None = None
    confidence: Confidence = Confidence.EXACT
    name: str = ""


_UNKNOWN = _Type("unknown")


def _weakest(*levels: Confidence) -> Confidence:
    return min(levels, key=lambda c: _RANK[c])


def _base(type_text: str) -> str:
    return _GENERICS.sub("", type_text).removesuffix("...")


@dataclass
class _Index:
    nodes: dict[str, Node] = field(default_factory=dict)
    parent: dict[str, str] = field(default_factory=dict)
    by_fqn: dict[str, str] = field(default_factory=dict)
    by_simple: dict[str, list[str]] = field(default_factory=dict)
    methods: dict[str, list[Node]] = field(default_factory=dict)
    fields: dict[str, dict[str, str]] = field(default_factory=dict)


class JavaResolver:
    def __init__(self, out: Extraction, sites: list[CallSite], locals_: dict[str, dict[str, str]]):
        self.out = out
        self.sites = sites
        self.locals = locals_
        self.ix = _Index()
        self.edges: dict[str, Edge] = {}
        self.library: dict[str, set[str]] = {}
        self.unresolved: dict[str, set[str]] = {}
        self._results: dict[int, _Type] = {}
        self._build_index()

    # ---- index -------------------------------------------------------------

    def _build_index(self) -> None:
        ix = self.ix
        for n in self.out.nodes:
            ix.nodes[n.id] = n
            if n.kind == NodeKind.CLASS:
                ix.by_fqn.setdefault(n.attributes["qualified_name"], n.id)
                ix.by_simple.setdefault(n.name, []).append(n.id)
        for e in self.out.edges:
            if e.kind == "contains":
                ix.parent[e.dst] = e.src
                child = ix.nodes[e.dst]
                if child.kind == NodeKind.METHOD:
                    ix.methods.setdefault(e.src, []).append(child)
                elif child.kind == NodeKind.FIELD:
                    ix.fields.setdefault(e.src, {})[child.name] = child.attributes["type"]

    def _top_level(self, class_id: str) -> Node:
        while (p := self.ix.parent.get(class_id)) is not None:
            class_id = p
        return self.ix.nodes[class_id]

    def _enclosing(self, class_id: str) -> list[str]:
        """The class and its outer classes, innermost first."""
        chain = [class_id]
        while (p := self.ix.parent.get(chain[-1])) is not None:
            chain.append(p)
        return chain

    # ---- types -------------------------------------------------------------

    def resolve_type(self, type_text: str, from_class: str) -> _Type:
        base = _base(type_text)
        if not base or base in _PRIMITIVES:
            return _UNKNOWN
        if base.endswith("[]"):
            return _Type("library", name=base)
        if base in self.ix.by_fqn:
            return _Type("repo", self.ix.by_fqn[base], name=base.rsplit(".", 1)[-1])
        head, _, rest = base.partition(".")
        found = self._resolve_simple(head, from_class)
        if found.status == "repo" and found.class_id and rest:
            qualified = self.ix.nodes[found.class_id].attributes["qualified_name"]
            nested = self.ix.by_fqn.get(f"{qualified}.{rest}")
            if nested is None:
                return _UNKNOWN
            return _Type("repo", nested, found.confidence, rest.rsplit(".", 1)[-1])
        return found

    def _resolve_simple(self, simple: str, from_class: str) -> _Type:
        # Nested classes visible from here (own, outer classes' members).
        for cid in self._enclosing(from_class):
            qualified = self.ix.nodes[cid].attributes["qualified_name"]
            if self.ix.nodes[cid].name == simple:
                return _Type("repo", cid, name=simple)
            if (nested := self.ix.by_fqn.get(f"{qualified}.{simple}")) is not None:
                return _Type("repo", nested, name=simple)
        top = self._top_level(from_class)
        imports = [i for i in top.attributes.get("imports", []) if not i.startswith("static ")]
        for imp in imports:
            if imp.endswith(f".{simple}"):
                hit = self.ix.by_fqn.get(imp)
                return _Type("repo", hit, name=simple) if hit else _Type("library", name=simple)
        package = top.attributes["package"]
        if (hit := self.ix.by_fqn.get(f"{package}.{simple}" if package else simple)) is not None:
            return _Type("repo", hit, name=simple)
        for imp in imports:
            if imp.endswith(".*") and (hit := self.ix.by_fqn.get(f"{imp[:-2]}.{simple}")):
                return _Type("repo", hit, name=simple)
        candidates = self.ix.by_simple.get(simple, [])
        if len(candidates) == 1:
            return _Type("repo", candidates[0], Confidence.LIKELY, simple)
        if len(candidates) > 1:
            return _Type("repo", candidates[0], Confidence.GUESS, simple)
        return _Type("library", name=simple)

    def _supertypes(self, class_id: str) -> list[tuple[str, _Type]]:
        node = self.ix.nodes[class_id]
        raw = node.attributes["extends"] + node.attributes["implements"]
        return [(r, self.resolve_type(r, class_id)) for r in raw]

    # ---- methods -----------------------------------------------------------

    @staticmethod
    def _arity_matches(method: Node, arity: int) -> bool:
        params = method.attributes["parameters"]
        if params and params[-1]["type"].endswith("..."):
            return arity >= len(params) - 1
        return arity == len(params)

    def find_methods(
        self, class_id: str, name: str, arity: int, seen: set[str] | None = None
    ) -> tuple[list[Node], str | None]:
        """Methods named `name` in class_id or its repo supertypes; else a library supertype."""
        seen = seen if seen is not None else set()
        if class_id in seen:
            return [], None
        seen.add(class_id)
        own = [
            m
            for m in self.ix.methods.get(class_id, [])
            if m.name == name and not m.attributes["constructor"] and self._arity_matches(m, arity)
        ]
        if own:
            return own, None
        library_super = None
        for raw, sup in self._supertypes(class_id):
            if sup.status == "repo" and sup.class_id:
                found, lib = self.find_methods(sup.class_id, name, arity, seen)
                if found:
                    return found, None
                library_super = library_super or lib
            elif sup.status == "library":
                library_super = library_super or _base(raw)
        return [], library_super

    def _field_type(self, class_id: str, name: str) -> tuple[str, str] | None:
        for cid in self._enclosing(class_id):
            if name in self.ix.fields.get(cid, {}):
                return self.ix.fields[cid][name], cid
            for _, sup in self._supertypes(cid):
                if sup.class_id and name in self.ix.fields.get(sup.class_id, {}):
                    return self.ix.fields[sup.class_id][name], sup.class_id
        return None

    # ---- edges -------------------------------------------------------------

    def _edge(
        self, kind: EdgeKind, src: str, dst: str, conf: Confidence, file: str, line: int
    ) -> None:
        edge_id = f"{kind}:{src}>{dst}"
        old = self.edges.get(edge_id)
        if old is None or _RANK[conf] > _RANK[old.confidence]:
            self.edges[edge_id] = Edge(edge_id, src, dst, kind, conf, file, line, _RESOLVER)

    def _note(self, bucket: dict[str, set[str]], method_id: str, text: str) -> None:
        bucket.setdefault(method_id, set()).add(text)

    # ---- calls -------------------------------------------------------------

    def _receiver_type(self, site: CallSite) -> _Type | None:
        """Type of the receiver; None means an implicit call (own or outer class)."""
        cls = site.class_id
        match site.receiver:
            case "implicit":
                return None
            case "this":
                return _Type("repo", cls, name=self.ix.nodes[cls].name)
            case "super":
                for raw, sup in self._supertypes(cls):
                    if self.ix.nodes[cls].attributes["extends"][:1] == [raw]:
                        return sup
                return _UNKNOWN
            case "field":
                found = self._field_type(cls, site.receiver_text)
                return self.resolve_type(*found) if found else _UNKNOWN
            case "name":
                local = self.locals.get(site.method_id, {}).get(site.receiver_text)
                if local is not None:
                    return self.resolve_type(local, cls)
                found = self._field_type(cls, site.receiver_text)
                if found is not None:
                    return self.resolve_type(*found)
                if site.receiver_text[:1].isupper():  # static call: Type.method()
                    return self.resolve_type(site.receiver_text, cls)
                return _UNKNOWN
            case "call":
                return self.resolve_site(site.inner) if site.inner else _UNKNOWN
            case "new":
                return self.resolve_type(site.receiver_text, cls)
        return _UNKNOWN

    def resolve_site(self, site: CallSite) -> _Type:
        """Resolve a call, add its edge or note, and return the type of its result."""
        key = id(site)
        if key not in self._results:
            self._results[key] = self._resolve(site)
        return self._results[key]

    def _resolve(self, site: CallSite) -> _Type:
        src, cls = site.method_id, site.class_id
        if site.kind == "new":
            created = self.resolve_type(site.name, cls)
            if created.status == "repo" and created.class_id:
                ctors = [
                    m
                    for m in self.ix.methods.get(created.class_id, [])
                    if m.attributes["constructor"] and self._arity_matches(m, site.arity)
                ]
                targets = [c.id for c in ctors] or [created.class_id]
                conf = created.confidence if len(targets) == 1 else Confidence.LIKELY
                for t in targets:
                    self._edge(EdgeKind.CALLS, src, t, _weakest(conf, created.confidence),
                               site.file, site.line)  # fmt: skip
            elif created.status == "library":
                self._note(self.library, src, f"new {created.name}")
            return created

        receiver = self._receiver_type(site)
        if receiver is None:
            return self._resolve_implicit(site)
        if receiver.status == "repo" and receiver.class_id:
            return self._call_into(site, receiver.class_id, receiver.confidence, receiver.name)
        if receiver.status == "library":
            self._note(self.library, src, f"{receiver.name or '(library)'}.{site.name}")
            return _Type("library")
        return self._guess(site)

    def _resolve_implicit(self, site: CallSite) -> _Type:
        for cid in self._enclosing(site.class_id):
            found, lib = self.find_methods(cid, site.name, site.arity)
            if found or lib:
                return self._call_into(site, cid, Confidence.EXACT, self.ix.nodes[cid].name)
        top = self._top_level(site.class_id)
        for imp in top.attributes.get("imports", []):
            if imp.startswith("static ") and (imp.endswith(f".{site.name}") or imp.endswith(".*")):
                owner = imp.removeprefix("static ").rsplit(".", 1)[0]
                if (owner_id := self.ix.by_fqn.get(owner)) is not None:
                    label = owner.rsplit(".", 1)[-1]
                    return self._call_into(site, owner_id, Confidence.EXACT, label)
                if imp.endswith(f".{site.name}"):
                    self._note(
                        self.library, site.method_id, f"{owner.rsplit('.', 1)[-1]}.{site.name}"
                    )
                    return _Type("library")
        return self._guess(site)

    def _call_into(self, site: CallSite, class_id: str, type_conf: Confidence, label: str) -> _Type:
        found, library_super = self.find_methods(class_id, site.name, site.arity)
        if len(found) == 1:
            target = found[0]
            self._edge(EdgeKind.CALLS, site.method_id, target.id, type_conf, site.file, site.line)
            declaring = self.ix.parent[target.id]
            result = self.resolve_type(target.attributes["return_type"] or "", declaring)
            result.confidence = _weakest(result.confidence, type_conf)
            return result
        if found:
            conf = _weakest(type_conf, Confidence.LIKELY)
            results = []
            for m in found:
                self._edge(EdgeKind.CALLS, site.method_id, m.id, conf, site.file, site.line)
                declaring = self.ix.parent[m.id]
                results.append(self.resolve_type(m.attributes["return_type"] or "", declaring))
            # Ambiguous overload, but if every candidate returns the same type, keep it.
            if results[0].status == "repo" and all(
                r.class_id == results[0].class_id for r in results
            ):
                return _Type("repo", results[0].class_id, conf, results[0].name)
            return _UNKNOWN
        if library_super:
            conf = _weakest(type_conf, Confidence.LIKELY)
            self._edge(EdgeKind.CALLS, site.method_id, class_id, conf, site.file, site.line)
            self._note(
                self.library,
                site.method_id,
                f"{label}.{site.name} (inherited from {library_super})",
            )
            return _Type("library")
        self._note(self.unresolved, site.method_id, f"{label}.{site.name}")
        return _UNKNOWN

    def _guess(self, site: CallSite) -> _Type:
        candidates = [
            m
            for ms in self.ix.methods.values()
            for m in ms
            if m.name == site.name and not m.attributes["constructor"]
            and self._arity_matches(m, site.arity)
        ]  # fmt: skip
        if len(candidates) == 1:
            self._edge(EdgeKind.CALLS, site.method_id, candidates[0].id, Confidence.GUESS,
                       site.file, site.line)  # fmt: skip
        else:
            self._note(self.unresolved, site.method_id, f"?.{site.name}")
        return _UNKNOWN

    # ---- supertypes, overrides, injection ----------------------------------

    def _structure(self) -> None:
        classes = [n for n in self.out.nodes if n.kind == NodeKind.CLASS]
        for cls in classes:
            is_interface = cls.attributes["type_kind"] == "interface"
            for raw in cls.attributes["extends"]:
                sup = self.resolve_type(raw, cls.id)
                if sup.status == "repo" and sup.class_id:
                    self._edge(EdgeKind.EXTENDS, cls.id, sup.class_id, sup.confidence, cls.file,
                               cls.start_line)  # fmt: skip
            for raw in cls.attributes["implements"]:
                sup = self.resolve_type(raw, cls.id)
                if sup.status == "repo" and sup.class_id:
                    kind = EdgeKind.EXTENDS if is_interface else EdgeKind.IMPLEMENTS
                    self._edge(kind, cls.id, sup.class_id, sup.confidence, cls.file,
                               cls.start_line)  # fmt: skip
            self._overrides(cls)
            self._injection(cls)

    def _overrides(self, cls: Node) -> None:
        own = [m for m in self.ix.methods.get(cls.id, []) if not m.attributes["constructor"]]
        if not own:
            return
        pending = [s for _, s in self._supertypes(cls.id)]
        seen: set[str] = set()
        while pending:
            sup = pending.pop()
            if sup.status != "repo" or not sup.class_id or sup.class_id in seen:
                continue
            seen.add(sup.class_id)
            for m in own:
                for sm in self.ix.methods.get(sup.class_id, []):
                    if sm.name != m.name or sm.attributes["constructor"]:
                        continue
                    types = [p["type"] for p in m.attributes["parameters"]]
                    super_types = [p["type"] for p in sm.attributes["parameters"]]
                    if types == super_types:
                        conf = sup.confidence
                    elif len(types) == len(super_types):
                        conf = _weakest(sup.confidence, Confidence.LIKELY)
                    else:
                        continue
                    self._edge(EdgeKind.OVERRIDES, m.id, sm.id, conf, m.file, m.start_line)
            pending += [s for _, s in self._supertypes(sup.class_id)]

    def _injection(self, cls: Node) -> None:
        annotations = set(cls.attributes["annotations"])
        fields = [
            f
            for f in self.out.nodes
            if f.kind == NodeKind.FIELD and self.ix.parent.get(f.id) == cls.id
        ]
        injected: list[tuple[str, Confidence, int]] = []
        for f in fields:
            if _INJECT_ANNOTATIONS & set(f.attributes["annotations"]):
                injected.append((f.attributes["type"], Confidence.EXACT, f.start_line))
        if annotations & _STEREOTYPES:
            ctors = [m for m in self.ix.methods.get(cls.id, []) if m.attributes["constructor"]]
            chosen = [c for c in ctors if "Autowired" in c.attributes["annotations"]]
            for ctor in chosen or (ctors if len(ctors) == 1 else []):
                for p in ctor.attributes["parameters"]:
                    injected.append((p["type"], Confidence.EXACT, ctor.start_line))
            # Lombok generates the constructor, so we can't see it: infer from fields.
            if not ctors and annotations & {"RequiredArgsConstructor", "AllArgsConstructor"}:
                all_args = "AllArgsConstructor" in annotations
                for f in fields:
                    mods = f.attributes["modifiers"]
                    if "static" not in mods and (all_args or "final" in mods):
                        injected.append((f.attributes["type"], Confidence.LIKELY, f.start_line))
        for type_text, conf, line in injected:
            target = self.resolve_type(type_text, cls.id)
            if target.status == "repo" and target.class_id:
                self._edge(EdgeKind.INJECTS, cls.id, target.class_id,
                           _weakest(conf, target.confidence), cls.file, line)  # fmt: skip

    # ---- run ---------------------------------------------------------------

    def run(self) -> None:
        self._structure()
        for site in self.sites:
            self.resolve_site(site)
        self.out.edges += sorted(self.edges.values(), key=lambda e: e.id)
        for method_id, calls in self.library.items():
            self.ix.nodes[method_id].attributes["library_calls"] = sorted(calls)
        for method_id, calls in self.unresolved.items():
            self.ix.nodes[method_id].attributes["unresolved_calls"] = sorted(calls)
