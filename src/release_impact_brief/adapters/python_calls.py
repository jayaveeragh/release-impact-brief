"""Cross-file resolution for Python: imports, calls, base classes and overrides.

Syntax only; nothing is imported or run. Each edge says how sure it is:
- exact: the name is defined in this module, or imported from a repo module by name
  (`from app import crud; crud.create_user()`), or `self.m()` found on the class itself;
- likely: found through a type hint, a local assigned from a constructor, or a base
  class (`self.m()` inherited) - Python doesn't enforce hints and subclasses can override;
- guess: the receiver is unknown and exactly one method in the repo has that name.
Calls into code outside the repo (the standard library, FastAPI, SQLAlchemy, ...) are
listed on the method as `library_calls`; calls that can't be resolved at all are listed
as `unresolved_calls`. Dynamic dispatch (`getattr`, `**kwargs` handlers) is never guessed.
"""

import builtins
import re
from dataclasses import dataclass

from release_impact_brief.adapters import Extraction
from release_impact_brief.facts import Confidence, Edge, EdgeKind, NodeKind

_RESOLVER = "python-calls"
_BUILTINS = set(dir(builtins))
_RANK = {Confidence.EXACT: 3, Confidence.LIKELY: 2, Confidence.GUESS: 1, Confidence.UNKNOWN: 0}
_WRAPPERS = re.compile(r"^(?:typing\.)?(?:Optional|Annotated|Type|ClassVar|Final)\[(.*)\]$")


def weakest(*levels: Confidence) -> Confidence:
    return min(levels, key=lambda c: _RANK[c])


@dataclass
class PyCallSite:
    """One call inside a function body, recorded at parse time and resolved later."""

    method_id: str
    owner_id: str  # the class (or module) the function belongs to
    file: str
    line: int
    name: str
    receiver: str | None  # None: a bare `f()`; "<expr>": a call on a computed value
    in_class: bool


@dataclass
class Target:
    """What an expression refers to."""

    kind: str  # "node" (a repo fact), "instance" (of a repo class), "module", "library"
    ref: str  # node id, class id, module id, or the dotted library name
    confidence: Confidence = Confidence.EXACT


def base_type(type_text: str) -> str:
    """`Optional[User]`, `User | None`, `"User"`, `Annotated[User, ...]`, `list[User]` -> `User`."""
    t = type_text.strip().strip("'\"")
    while (m := _WRAPPERS.match(t)) is not None:
        t = m.group(1).split(",", 1)[0].strip().strip("'\"")
    parts = [p.strip() for p in t.split("|") if p.strip() not in ("None", "")]
    t = parts[0] if parts else ""
    if "[" in t:  # list[User], Mapped[User]: the item type
        inner = t.split("[", 1)[1].rsplit("]", 1)[0]
        return base_type(inner.split(",")[-1])
    return t


class PythonResolver:
    def __init__(self, out: Extraction, sites: list[PyCallSite],
                 scopes: dict[str, dict[str, str]]) -> None:  # fmt: skip
        self.out, self.sites, self.scopes = out, sites, scopes
        self.nodes = {n.id: n for n in out.nodes}
        self.members: dict[str, dict[str, str]] = {}  # class/module id -> name -> member id
        self.parent: dict[str, str] = {}
        for e in out.edges:
            if e.kind == EdgeKind.CONTAINS and e.dst in self.nodes:
                self.parent[e.dst] = e.src
                self.members.setdefault(e.src, {}).setdefault(self.nodes[e.dst].name, e.dst)
        self.modules = {n.attributes["qualified_name"]: n.id for n in out.nodes
                        if n.kind == NodeKind.CLASS and n.attributes.get("type_kind") == "module"
                        and n.language == "python"}  # fmt: skip
        self.methods_by_name: dict[str, list[str]] = {}
        for n in out.nodes:
            if n.kind == NodeKind.METHOD and n.language == "python":
                self.methods_by_name.setdefault(n.name, []).append(n.id)
        self.bases: dict[str, list[tuple[str, Confidence]]] = {}
        self.edges: dict[str, Edge] = {}
        self._module_cache: dict[tuple[str, str], str | None] = {}

    # ---- modules and names -------------------------------------------------

    def module_of(self, node_id: str) -> str:
        current = node_id
        while (
            current in self.parent and self.nodes[current].attributes.get("type_kind") != "module"
        ):
            current = self.parent[current]
        return current

    def find_module(self, dotted: str, near: str) -> str | None:
        """The repo module a dotted import names. Repos import without their source folder
        (`app.models` for `backend/app/models.py`), so a unique suffix match counts; on a
        tie the module closest to the importer wins."""
        key = (dotted, near)
        if key in self._module_cache:
            return self._module_cache[key]
        found = self.modules.get(dotted)
        if found is None:
            matches = [q for q in self.modules if q.endswith("." + dotted)]
            if matches:
                here = self.nodes[near].attributes["qualified_name"]
                matches.sort(key=lambda q: (-_common(q, here), len(q)))
                found = self.modules[matches[0]]
        self._module_cache[key] = found
        return found

    def resolve_dotted(self, dotted: str, near: str) -> Target:
        """An absolute dotted name (`app.models.User`): a repo module, a member, or library."""
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            module = self.find_module(".".join(parts[:i]), near)
            if module is None:
                continue
            target = Target("module", module)
            for attr in parts[i:]:
                next_target = self.member(target, attr)
                if next_target is None:
                    return Target("library", dotted, Confidence.UNKNOWN)
                target = next_target
            return target
        return Target("library", dotted)

    def resolve_name(self, name: str, module_id: str) -> Target | None:
        member = self.members.get(module_id, {}).get(name)
        if member is not None:
            return self.as_target(member, Confidence.EXACT)
        alias = self.nodes[module_id].attributes.get("aliases", {}).get(name)
        if alias is not None:
            return self.resolve_dotted(alias, module_id)
        if name in _BUILTINS:
            return Target("library", f"builtins.{name}")
        return None

    def field(self, dotted: str, module_id: str) -> str | None:
        """The variable or class attribute `a.b.c` names (not what it holds), if any:
        `router`, `items.router`, `settings.API_V1_STR`."""
        head, _, attr = dotted.rpartition(".")
        if not head:
            member = self.members.get(module_id, {}).get(attr)
            if member is None and (alias := self.nodes[module_id].attributes.get(
                    "aliases", {}).get(attr)) is not None:  # fmt: skip
                parent, _, name = alias.rpartition(".")
                found = self.resolve_dotted(parent, module_id) if parent else None
                member = self.members.get(found.ref, {}).get(name) if found is not None and \
                    found.kind == "module" else None  # fmt: skip
        else:
            found = self.expr(head, module_id, {})
            if found is None or found.kind == "library":
                return None
            owner = found.ref
            member = self.members.get(owner, {}).get(attr)
            if member is None and self.nodes[owner].kind == NodeKind.CLASS:
                on_class = self.class_member(owner, attr)
                member = on_class[0] if on_class is not None else None
        if member is not None and self.nodes[member].kind == NodeKind.FIELD:
            return member
        return None

    def as_target(self, node_id: str, confidence: Confidence) -> Target:
        """A module-level variable stands for what it holds: `settings = Settings()` -> a
        Settings instance; `crud = other_module` isn't followed."""
        node = self.nodes[node_id]
        if node.kind == NodeKind.FIELD and (node.attributes.get("call") or "").endswith(
                ("relationship", "Relationship")):  # fmt: skip
            return Target("library", "sqlalchemy.orm.relationship()")
        if node.kind == NodeKind.FIELD:
            hinted = self.type_target(node.attributes.get("type", ""), self.module_of(node_id))
            if hinted is not None:
                return hinted
            call = node.attributes.get("call")
            if call:
                made = self.expr(call, self.module_of(node_id), {})
                if made is not None and made.kind == "node" and \
                        self.nodes[made.ref].kind == NodeKind.CLASS:  # fmt: skip
                    return Target("instance", made.ref, weakest(confidence, made.confidence))
                if made is not None and made.kind == "library":
                    return Target("library", f"{made.ref}()")
        return Target("node", node_id, confidence)

    def type_target(self, type_text: str, module_id: str) -> Target | None:
        """The repo class (as an instance) or library type a hint names."""
        base = base_type(type_text)
        if not base:
            return None
        found = self.expr(base, module_id, {})
        if found is None:
            return None
        if found.kind == "node" and self.nodes[found.ref].kind == NodeKind.CLASS:
            return Target("instance", found.ref, Confidence.LIKELY)
        if found.kind == "node" and self.nodes[found.ref].kind == NodeKind.FIELD:
            # a type alias: `SessionDep = Annotated[Session, Depends(get_db)]`
            value = self.nodes[found.ref].attributes.get("value", "")
            if value.startswith(("Annotated[", "Optional[")) and value != type_text:
                return self.type_target(value, self.module_of(found.ref))
        if found.kind == "library":
            return Target("library", found.ref)
        return None

    def member(self, target: Target, attr: str) -> Target | None:
        if target.kind == "library":
            return Target("library", f"{target.ref}.{attr}")
        if target.kind == "module":
            own = self.members.get(target.ref, {}).get(attr)
            if own is not None:
                return self.as_target(own, target.confidence)
            submodule = self.find_module(
                f"{self.nodes[target.ref].attributes['qualified_name']}.{attr}", target.ref
            )
            if submodule is not None:
                return Target("module", submodule, target.confidence)
            alias = self.nodes[target.ref].attributes.get("aliases", {}).get(attr)
            if alias is not None:  # re-exported: `from .models import User` in __init__
                found = self.resolve_dotted(alias, target.ref)
                return Target(found.kind, found.ref, weakest(found.confidence, target.confidence))
            return None
        if target.kind in ("node", "instance") and self.nodes[target.ref].kind == NodeKind.CLASS:
            on_class = self.class_member(target.ref, attr)
            if on_class is not None:
                member_id, via_base = on_class
                level = Confidence.LIKELY if via_base or target.kind == "instance" else \
                    Confidence.EXACT  # fmt: skip
                return self.as_target(member_id, weakest(level, target.confidence))
            library_base = self.library_base(target.ref)
            if library_base is not None:
                return Target("library", f"{library_base}.{attr}")
            return None
        return None

    def class_bases(self, class_id: str) -> list[tuple[str, Confidence]]:
        if class_id not in self.bases:
            self.bases[class_id] = []
            module = self.module_of(class_id)
            for raw in self.nodes[class_id].attributes.get("extends", []):
                found = self.expr(raw.split("[", 1)[0], module, {})
                if found is not None and found.kind == "node" and \
                        self.nodes[found.ref].kind == NodeKind.CLASS:  # fmt: skip
                    self.bases[class_id].append((found.ref, found.confidence))
        return self.bases[class_id]

    def library_bases(self, class_id: str, seen: frozenset[str] = frozenset()) -> list[str]:
        """Base classes (here or inherited) from outside the repo, by dotted name, in order."""
        if class_id in seen:
            return []
        module = self.module_of(class_id)
        found_bases = []
        for raw in self.nodes[class_id].attributes.get("extends", []):
            found = self.expr(raw.split("[", 1)[0], module, {})
            if found is None or found.kind == "library":
                found_bases.append(found.ref if found is not None else raw)
        for base, _ in self.class_bases(class_id):
            found_bases += self.library_bases(base, seen | {class_id})
        return found_bases

    def library_base(self, class_id: str) -> str | None:
        """The first base class from outside the repo."""
        return next(iter(self.library_bases(class_id)), None)

    def class_member(self, class_id: str, attr: str,
                     seen: frozenset[str] = frozenset()) -> tuple[str, bool] | None:  # fmt: skip
        """(member id, found on a base class) - own members first, then bases in order."""
        own = self.members.get(class_id, {}).get(attr)
        if own is not None:
            return own, bool(seen)
        for base, _ in self.class_bases(class_id):
            if base not in seen and (found := self.class_member(base, attr, seen | {class_id})):
                return found[0], True
        return None

    def called(self, target: Target | None) -> Target | None:
        """What calling the target gives: a repo class -> an instance, a function -> what
        its return hint names, library -> library."""
        if target is None:
            return None
        if target.kind == "library":
            return Target("library", f"{target.ref}()")
        node = self.nodes[target.ref]
        if target.kind == "node" and node.kind == NodeKind.CLASS:
            return Target("instance", node.id, weakest(Confidence.LIKELY, target.confidence))
        if node.kind == NodeKind.METHOD:
            return self.type_target(node.attributes.get("return_type") or "",
                                    self.module_of(node.id))  # fmt: skip
        return None

    def expr(self, dotted: str, module_id: str, scope: dict[str, str]) -> Target | None:
        """What `a.b.c` refers to inside a function with these local types. A part may end
        in `()` (called) or `[]` (indexed): `sa.select().where`."""
        target: Target | None = None
        for i, token in enumerate(dotted.split(".")):
            name = token.rstrip("()[]")
            if i == 0:
                local = scope.get(name)
                if local is None:
                    target = self.resolve_name(name, module_id)
                elif local.startswith("\x01"):  # self / cls
                    target = Target("instance" if local[1] == "i" else "node", local[2:])
                elif local.startswith("()"):
                    target = self.expr(local[2:], module_id, scope if local[2:] != token else {})
                else:
                    target = self.type_target(local, module_id)
            elif target is not None:
                target = self.member(target, name)
            if target is None:
                return None
            for mark in token[len(name) :].replace(")", "").replace("]", ""):
                target = self.called(target) if mark == "(" else (
                    target if target.kind == "library" else None)  # fmt: skip
                if target is None:
                    return None
        return target

    # ---- the passes -----------------------------------------------------------

    def run(self) -> None:
        for n in [n for n in self.out.nodes if n.kind == NodeKind.CLASS and n.language == "python"
                  and n.attributes.get("type_kind") == "class"]:  # fmt: skip
            for base, conf in self.class_bases(n.id):
                self._edge(EdgeKind.EXTENDS, n.id, base, conf, n.file, n.start_line)
            for name, member in self.members.get(n.id, {}).items():
                if self.nodes[member].kind != NodeKind.METHOD or name.startswith("__"):
                    continue
                for base, conf in self.class_bases(n.id):
                    found = self.class_member(base, name)
                    if found is not None and self.nodes[found[0]].kind == NodeKind.METHOD:
                        m = self.nodes[member]
                        self._edge(EdgeKind.OVERRIDES, member, found[0], conf, m.file, m.start_line)
                        break
        library: dict[str, list[str]] = {}
        unresolved: dict[str, list[str]] = {}
        for site in self.sites:
            target, conf, label = self.call_target(site)
            if target is not None:
                self._edge(EdgeKind.CALLS, site.method_id, target, conf, site.file, site.line)
            elif label.startswith("lib:"):
                calls = library.setdefault(site.method_id, [])
                if label[4:] not in calls:
                    calls.append(label[4:])
            else:
                unresolved.setdefault(site.method_id, []).append(f"{label} (line {site.line})")
        for method_id, calls in library.items():
            self.nodes[method_id].attributes["library_calls"] = calls
        for method_id, calls in unresolved.items():
            self.nodes[method_id].attributes["unresolved_calls"] = calls
        self.out.edges += self.edges.values()

    def call_target(self, site: PyCallSite) -> tuple[str | None, Confidence, str]:
        """(method id, confidence, label); label is `lib:<name>` for a library call."""
        module = self.module_of(site.method_id)
        scope = self.scopes.get(site.method_id, {})
        written = site.name if site.receiver is None else f"{site.receiver}.{site.name}"
        if site.receiver is None:
            found = self.expr(site.name, module, scope) if site.name not in scope else None
        elif site.receiver in ("self", "cls") and site.in_class:
            found = self.member(Target("node", site.owner_id), site.name)
        elif site.receiver == "<expr>":
            found = None
        elif site.receiver == "super()" and site.in_class:
            on_base = [self.class_member(b, site.name) for b, _ in self.class_bases(site.owner_id)]
            hit = next((m for m in on_base if m is not None), None)
            found = Target("node", hit[0], Confidence.LIKELY) if hit is not None else (
                Target("library", f"super().{site.name}"))  # fmt: skip
        else:
            found = self.expr(written, module, scope)
        if found is not None and found.kind == "library":
            return None, Confidence.UNKNOWN, f"lib:{found.ref.removeprefix('builtins.')}"
        if found is not None and found.kind in ("node", "instance"):
            node = self.nodes[found.ref]
            if node.kind == NodeKind.METHOD:
                return node.id, found.confidence, written
            if node.kind == NodeKind.CLASS:  # a constructor call
                init = self.class_member(node.id, "__init__")
                if init is not None:
                    level = Confidence.LIKELY if init[1] else found.confidence
                    return init[0], weakest(level, found.confidence), written
                return None, Confidence.UNKNOWN, f"lib:{written}"  # nothing of ours runs
        if site.receiver is not None and found is None:
            candidates = [m for m in self.methods_by_name.get(site.name, [])
                          if not site.name.startswith("__")]  # fmt: skip
            if len(candidates) == 1 and self._receiver_unknown(site, module, scope):
                return candidates[0], Confidence.GUESS, written
        return None, Confidence.UNKNOWN, written

    def _receiver_unknown(self, site: PyCallSite, module: str, scope: dict[str, str]) -> bool:
        """True when nothing is known about the receiver: a guess is allowed only then."""
        if site.receiver in (None, "<expr>", "super()"):
            return site.receiver == "<expr>"
        if "(" in site.receiver or "[" in site.receiver:
            return self.expr(site.receiver, module, scope) is None
        head = site.receiver.split(".")[0]
        if head in scope:
            return self.expr(site.receiver, module, scope) is None
        return self.resolve_name(head, module) is None

    def _edge(self, kind: EdgeKind, src: str, dst: str, conf: Confidence, file: str,
              line: int) -> None:  # fmt: skip
        edge_id = f"{kind}:{src}>{dst}"
        old = self.edges.get(edge_id)
        if old is None or _RANK[conf] > _RANK[old.confidence]:
            self.edges[edge_id] = Edge(edge_id, src, dst, kind, conf, file, line, _RESOLVER)


def _common(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a.split("."), b.split("."), strict=False):
        if x != y:
            break
        n += 1
    return n
