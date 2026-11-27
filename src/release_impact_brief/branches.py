"""Branch map for one endpoint: which request inputs drive which branches.

Built on demand from the handler's `flow` (adapters/java_flow.py) and the facts:
- A condition's reads are traced back to request inputs: through local variables
  (all their assignments, in any order - a variable's value may come from any of
  them), through getters to fields of the request object (`pet.getName()` ->
  `pet.name`, inherited fields included), and through calls whose arguments carry
  inputs (`search(page, name)` -> page, name).
- `result.hasErrors()` on a BindingResult means "did the @Valid object pass
  validation" (`validation:<input>`, whose fields are those with constraint
  annotations), plus the conditions of every earlier `rejectValue`.
- Inputs are classified: drives (some branch depends on it), passed through (only
  handed on to other code) or unused. A field of an object that a branch only reads
  as a whole (`owner.getPet(...)`) is "read as a whole": which fields that call
  reads isn't traced. Framework-supplied parameters aren't inputs.
Spring's custom validators (`@InitBinder` + setValidator) are named, not traced.
"""

import re
from collections.abc import Iterable
from typing import Any

from release_impact_brief.adapters.java_spring import describe_input
from release_impact_brief.facts import Edge, EdgeKind, Node, NodeKind

_CONSTRAINTS = {"NotBlank", "NotNull", "NotEmpty", "Size", "Pattern", "Min", "Max", "Email",
                "Past", "PastOrPresent", "Future", "FutureOrPresent", "Positive",
                "PositiveOrZero", "Negative", "NegativeOrZero", "Digits", "DecimalMin",
                "DecimalMax", "AssertTrue", "AssertFalse", "Valid"}  # fmt: skip
_BINDING = {"BindingResult", "Errors"}
_PYDANTIC_LIMITS = {"min_length", "max_length", "gt", "ge", "lt", "le", "pattern",
                    "max_digits", "decimal_places", "multiple_of"}  # fmt: skip
_ERROR_CHECKS = {"hasErrors", "hasFieldErrors", "hasGlobalErrors", "getErrorCount"}
_VALIDATION = "\x00validation"  # marker while resolving; replaced by validation:<input>
_FORM_CHECKS = {"validate_on_submit", "validate"}


def _base(type_text: str) -> str:
    return type_text.split("<", 1)[0].removesuffix("...").rsplit(".", 1)[-1]


def _short(node_id: str) -> str:
    return node_id.split(":", 2)[-1]


class _Facts:
    def __init__(self, nodes: Iterable[Node], edges: Iterable[Edge]) -> None:
        self.nodes = {n.id: n for n in nodes}
        self.edges = list(edges)
        self.children: dict[str, list[Node]] = {}
        self.parent: dict[str, str] = {}
        self.superclass: dict[str, str] = {}
        self.classes_by_name: dict[str, list[Node]] = {}
        for e in self.edges:
            if e.kind == EdgeKind.CONTAINS and e.dst in self.nodes:
                self.children.setdefault(e.src, []).append(self.nodes[e.dst])
                self.parent[e.dst] = e.src
            elif e.kind == EdgeKind.EXTENDS and ":class:" in e.src:
                self.superclass.setdefault(e.src, e.dst)
        for n in self.nodes.values():
            if n.kind == NodeKind.CLASS:
                self.classes_by_name.setdefault(n.name, []).append(n)

    def class_of_type(self, type_text: str, near: str) -> Node | None:
        """The repo class a type names; same package as `near` wins a tie."""
        candidates = self.classes_by_name.get(_base(type_text), [])
        if len(candidates) > 1:
            package = self.nodes[near].attributes.get("package")
            candidates = [
                c for c in candidates if c.attributes.get("package") == package
            ] or candidates
        return candidates[0] if candidates else None

    def fields(self, cls: Node) -> list[Node]:
        """Fields of a class, inherited ones first."""
        chain, current = [], cls.id
        while current and current not in chain:
            chain.append(current)
            current = self.superclass.get(current, "")
        members = [f for cid in reversed(chain) for f in self.children.get(cid, [])]
        return [
            f
            for f in members
            if f.kind == NodeKind.FIELD and "static" not in f.attributes["modifiers"]
        ]


def _property(method: str, field_names: set[str]) -> str | None:
    for prefix in ("get", "is"):
        if method.startswith(prefix) and len(method) > len(prefix):
            name = method[len(prefix)].lower() + method[len(prefix) + 1 :]
            if name in field_names:
                return name
    return None


def branch_map(nodes: Iterable[Node], edges: Iterable[Edge], endpoint_id: str) -> dict[str, Any]:
    facts = _Facts(nodes, edges)
    endpoint = facts.nodes[endpoint_id]  # KeyError: no such endpoint
    handler = facts.nodes[endpoint.attributes["handler"]]
    controller = facts.parent.get(handler.id, "")
    flow = handler.attributes.get("flow") or {
        "branches": [],
        "locals": [],
        "calls": [],
        "rejects": [],
        "returns": [],
        "throws": [],
    }
    params = handler.attributes["parameters"]

    # Request inputs, by parameter name (the request may know them by another name).
    inputs: dict[str, dict[str, Any]] = {}
    classes: dict[str, Node] = {}
    if handler.language == "python":
        # Python inputs are found by the adapter: parameters, `request.args[...]` reads
        # (keyed by their request ref) and form objects made in the handler.
        for entry in endpoint.attributes["inputs"]:
            key = entry.get("param") or entry.get("ref") or entry["name"]
            inputs[key] = {k: v for k, v in entry.items() if k not in ("param", "ref", "class")}
            cls = facts.nodes.get(entry.get("class", "")) or (
                facts.class_of_type(entry["type"], controller) if entry["source"] in (
                    "body", "form") else None)  # fmt: skip
            if cls is not None:
                classes[key] = cls
        binding: set[str] = set()
        valid = [k for k, e in inputs.items() if e.get("validated") and k in classes]
    else:
        for p in params:
            if (described := describe_input(p)) is not None:
                inputs[p["name"]] = described
                if (cls := facts.class_of_type(p["type"], controller)) is not None:
                    classes[p["name"]] = cls
        binding = {p["name"] for p in params if _base(p["type"]) in _BINDING}
        valid = [p["name"] for p in params if {"Valid", "Validated"} & set(p["annotations"])
                 and p["name"] in inputs]  # fmt: skip
    field_names = {name: {f.name for f in facts.fields(cls)} for name, cls in classes.items()}

    local_deps: dict[str, set[str]] = {}

    def resolve(ref: str) -> set[str]:
        if ref.startswith("request:"):  # Flask: request.args["page"] -> the `page` input
            return {inputs[ref]["name"]} if ref in inputs else set()
        head, dot, rest = ref.partition(".")
        if head in binding:
            return {_VALIDATION} if rest.split("(")[0] in _ERROR_CHECKS else set()
        if head in inputs:
            name = inputs[head]["name"]
            if not dot:
                return {name}
            method = rest.split("(")[0]
            if "(" not in rest:  # an attribute: user_in.email
                return {f"{name}.{rest}"}
            if method in _FORM_CHECKS and head in valid:
                return {f"validation:{name}"}  # WTForms: did the form pass its validators
            if rest.endswith("(...)"):
                return {name}  # a call with arguments on the object: depends on all of it
            prop = _property(method, field_names.get(head, set()))
            return {f"{name}.{prop}"} if prop else {f"{name}.{method}()"}
        if head in local_deps:
            return set(local_deps[head])
        return set()

    # Locals: everything any assignment reads (parameters keep their own identity).
    assignments = [a for a in flow["locals"] if a["name"] not in inputs]
    for name in {a["name"] for a in assignments}:
        local_deps[name] = set()
    for _ in range(3):  # enough rounds for chains like a -> b -> c
        for a in assignments:
            for ref in a["reads"]:
                local_deps[a["name"]] |= resolve(ref) - {_VALIDATION}

    branches = flow["branches"]
    direct = [set().union(*(resolve(r) for r in b["reads"])) if b["reads"] else set()
              for b in branches]  # fmt: skip

    def with_ancestors(index: int | None) -> set[str]:
        found: set[str] = set()
        while index is not None:
            found |= direct[index] - {_VALIDATION}
            index = branches[index]["parent"]
        return found

    out_branches = []
    for i, b in enumerate(branches):
        drivers = set(direct[i])
        if _VALIDATION in drivers:
            drivers.discard(_VALIDATION)
            if valid:
                drivers.add(f"validation:{inputs[valid[-1]]['name']}")
            for reject in flow["rejects"]:
                if reject["target"] in binding and reject["line"] < b["line"]:
                    drivers |= with_ancestors(reject["branch"])
        end = b.get("end_line", b["line"])
        calls = sorted({c["callee"] for c in flow["calls"] if b["line"] <= c["line"] <= end})
        entry = {k: v for k, v in b.items() if k != "reads"}
        out_branches.append(entry | {"drivers": sorted(drivers), "calls": calls})

    validation: dict[str, list[str]] = {}
    for name in valid:
        if name in classes:
            validation[inputs[name]["name"]] = sorted(
                f.name for f in facts.fields(classes[name]) if _constraints(f)
            )

    all_drivers = {d for b in out_branches for d in b["drivers"]}
    # handed to other code: an argument of a call, or the object a non-getter method is
    # called on (`item_in.model_dump()` hands its data on; `item.getCode()` only reads it)
    passed = {ref.split(".")[0] for c in flow["calls"] for ref in c["reads"]} | {
        head for c in flow["calls"] if "." in c["callee"]
        for head, method in [c["callee"].split(".")[:2]]
        if not re.match(r"(get|is|has)[A-Z_]", method)}  # fmt: skip
    rows = []
    for param_name, described in inputs.items():
        name = described["name"]
        validated_fields = (
            set(validation.get(name, [])) if f"validation:{name}" in all_drivers else set()
        )
        whole = name in all_drivers or any(d.startswith(f"{name}.") for d in all_drivers) \
            or bool(validated_fields)  # fmt: skip

        def role(drives: bool, param: str = param_name) -> str:
            return "drives" if drives else "passed through" if param in passed else "unused"

        rows.append(described | {"role": role(whole), "field": None,
                                 "lines": [b["line"] for b in out_branches
                                           if any(d == name or d.startswith(f"{name}.")
                                                  for d in b["drivers"])]})  # fmt: skip
        if param_name in classes:
            for f in facts.fields(classes[param_name]):
                key = f"{name}.{f.name}"
                drives = key in all_drivers or f.name in validated_fields
                field_lines = [b["line"] for b in out_branches if key in b["drivers"]
                               or (f.name in validated_fields
                                   and f"validation:{name}" in b["drivers"])]  # fmt: skip
                whole_lines = [b["line"] for b in out_branches if name in b["drivers"]]
                # A call on the whole object (owner.getPet(...)) may read any field: say so
                # rather than claim every field drives the branch.
                field_role = role(drives) if drives or not whole_lines else "read as a whole"
                rows.append(
                    {
                        "name": key,
                        "type": f.attributes["type"],
                        "source": described["source"],
                        "field": f.name,
                        "file": f.file,
                        "line": f.start_line,
                        "constraints": _constraints(f),
                        "role": field_role,
                        "lines": field_lines or (whole_lines if not drives else []),
                    }
                )

    return {
        "endpoint": endpoint.id,
        "name": endpoint.name,
        "handler": _short(handler.id),
        "file": handler.file,
        "line": handler.start_line,
        "end_line": handler.end_line,
        "inputs": rows,
        "branches": out_branches,
        "returns": sorted(flow["returns"]),
        "throws": sorted(flow["throws"]),
        "validation": validation,
        "custom_validator": _custom_validator(facts, controller),
        "library_calls": handler.attributes.get("library_calls", []),
        "unresolved_calls": handler.attributes.get("unresolved_calls", []),
    }


def _constraints(field: Node) -> list[str]:
    """Bean Validation annotations (Java), or the validators of a WTForms field
    (`StringField(..., validators=[DataRequired(), Length(max=64)])`) / the limits of a
    Pydantic `Field(min_length=...)` (Python)."""
    if field.language != "python":
        return sorted(_CONSTRAINTS & set(field.attributes["annotations"]))
    kwargs = field.attributes.get("call_kwargs", {})
    if "validators" in kwargs:
        return sorted(set(re.findall(r"(\w+)\(", kwargs["validators"])))
    if (field.attributes.get("call") or "").rsplit(".", 1)[-1] == "Field":
        return sorted(k for k in kwargs if k in _PYDANTIC_LIMITS)
    return []


def _custom_validator(facts: _Facts, controller: str) -> str | None:
    """A validator class installed by an @InitBinder method of the controller, if any."""
    binders = [m.id for m in facts.children.get(controller, [])
               if m.kind == NodeKind.METHOD
               and "InitBinder" in m.attributes["annotations"]]  # fmt: skip
    for e in facts.edges:
        if e.kind == EdgeKind.CALLS and e.src in binders:
            name = _short(e.dst).split("#", 1)[0].rsplit(".", 1)[-1]
            if name.endswith("Validator"):
                return name
    return None
