"""JPA entities, relations and Spring Data repositories (syntax only).

An entity node is created per `@Entity` class, linked from its class by a `maps`
edge. Relation fields (`@OneToMany`, ...) get a `relates` edge to the target
entity, and Spring Data repository interfaces a `stores` edge to theirs. An
annotation counts as JPA only if its import says so; without an import it is
`likely`, and an import from another package (e.g. a MongoDB `@Entity`) is ignored.
"""

from release_impact_brief.adapters import Extraction
from release_impact_brief.adapters.java_calls import JavaResolver, _weakest
from release_impact_brief.adapters.java_spring import _literal
from release_impact_brief.facts import Confidence, Edge, EdgeKind, Node, NodeKind

_RESOLVER = "java-jpa"
_JPA = ("jakarta.persistence", "javax.persistence")
_SPRING_DATA = ("org.springframework.data",)
_CLASS_ROLES = {"Entity": "entity", "MappedSuperclass": "mapped_superclass",
                "Embeddable": "embeddable"}  # fmt: skip
_RELATIONS = ("OneToOne", "OneToMany", "ManyToOne", "ManyToMany")
# Spring Data base interfaces whose first type argument is the entity.
_REPOSITORIES = {
    "Repository",
    "CrudRepository",
    "ListCrudRepository",
    "PagingAndSortingRepository",
    "ListPagingAndSortingRepository",
    "JpaRepository",
}


def _matches(raw: str, simple: str) -> bool:
    return raw == simple or raw.endswith(f".{simple}")


def _origin(raw: str, imports: list[str], packages: tuple[str, ...]) -> Confidence | None:
    """How sure we are that `raw` comes from one of `packages`; None means it doesn't."""
    package, _, simple = raw.rpartition(".")
    if package:
        return Confidence.EXACT if package.startswith(packages) else None
    explicit = [i for i in imports if i.endswith(f".{simple}") and not i.startswith("static ")]
    if explicit:
        return Confidence.EXACT if explicit[0].startswith(packages) else None
    if any(i.endswith(".*") and i.startswith(packages) for i in imports):
        return Confidence.EXACT
    return Confidence.LIKELY


def _arg(node: Node, simple: str, key: str) -> str | None:
    for raw, values in node.attributes.get("annotation_args", {}).items():
        if _matches(raw, simple) and values.get(key):
            return str(values[key][0])
    return None


def _type_args(type_text: str) -> list[str]:
    """Top-level type arguments: `Map<K,List<V>>` -> ["K", "List<V>"]."""
    if "<" not in type_text:
        return []
    inner = type_text[type_text.index("<") + 1 : type_text.rindex(">")]
    args, depth, start = [], 0, 0
    for i, ch in enumerate(inner):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(inner[start:i])
            start = i + 1
    args.append(inner[start:])
    return [a for a in args if a]


def _edge(kind: EdgeKind, src: str, dst: str, conf: Confidence, node: Node) -> Edge:
    return Edge(f"{kind}:{src}>{dst}", src, dst, kind, conf, node.file, node.start_line, _RESOLVER)


def add_entities(out: Extraction, resolver: JavaResolver) -> None:
    ix = resolver.ix

    def imports(class_id: str) -> list[str]:
        while (p := ix.parent.get(class_id)) is not None:
            class_id = p
        return list(ix.nodes[class_id].attributes.get("imports", []))

    classes = [n for n in out.nodes if n.kind == NodeKind.CLASS]
    fields_of: dict[str, list[Node]] = {}
    for e in out.edges:
        if e.kind == EdgeKind.CONTAINS and ix.nodes[e.dst].kind == NodeKind.FIELD:
            fields_of.setdefault(e.src, []).append(ix.nodes[e.dst])

    entity_of: dict[str, str] = {}  # class id -> entity id
    for cls in classes:
        for raw in cls.attributes["annotations"]:
            role = next((r for s, r in _CLASS_ROLES.items() if _matches(raw, s)), None)
            conf = _origin(raw, imports(cls.id), _JPA) if role else None
            if role is None or conf is None:
                continue
            cls.attributes["jpa"] = role
            if role != "entity":
                break
            if (table := _literal(_arg(cls, "Table", "name") or "")) is not None:
                source = "@Table"
            elif (table := _literal(_arg(cls, "Entity", "name") or "")) is not None:
                source = "@Entity(name)"
            else:
                table, source = cls.name, "default"
            entity_id = f"java:entity:{cls.attributes['qualified_name']}"
            entity_of[cls.id] = entity_id
            out.nodes.append(
                Node(entity_id, NodeKind.ENTITY, table, cls.file, cls.start_line, cls.end_line,
                     "java", {"class": cls.id, "table": table, "table_source": source})
            )  # fmt: skip
            out.edges.append(_edge(EdgeKind.MAPS, cls.id, entity_id, conf, cls))
            break

    for cls in classes:
        if "jpa" in cls.attributes:
            for f in fields_of.get(cls.id, []):
                _relation(f, cls.id, imports(cls.id), resolver, entity_of, out)
        if cls.attributes["type_kind"] == "interface":
            _repository(cls, imports(cls.id), resolver, entity_of, out)


def _relation(
    field: Node,
    class_id: str,
    imports: list[str],
    resolver: JavaResolver,
    entity_of: dict[str, str],
    out: Extraction,
) -> None:
    for raw in field.attributes["annotations"]:
        kind = next((r for r in _RELATIONS if _matches(raw, r)), None)
        conf = _origin(raw, imports, _JPA) if kind else None
        if kind is None or conf is None:
            continue
        target_entity = _arg(field, kind, "targetEntity")
        args = _type_args(field.attributes["type"])
        if target_entity:
            target = target_entity.removesuffix(".class")
        else:
            target = args[-1] if args else field.attributes["type"]  # Map<K, V> -> V
        field.attributes["relation"] = {"kind": kind, "target": target}
        found = resolver.resolve_type(target, class_id)
        if found.class_id is not None and found.class_id in entity_of:
            conf = _weakest(conf, found.confidence)
            out.edges.append(_edge(EdgeKind.RELATES, field.id, entity_of[found.class_id], conf,
                                   field))  # fmt: skip
        return


def _repository(
    cls: Node,
    imports: list[str],
    resolver: JavaResolver,
    entity_of: dict[str, str],
    out: Extraction,
) -> None:
    for raw in cls.attributes["extends"]:
        base = raw.split("<", 1)[0]
        simple = base.rsplit(".", 1)[-1]
        args = _type_args(raw)
        if simple not in _REPOSITORIES or not args:
            continue
        if resolver.resolve_type(raw, cls.id).status != "library":
            continue  # the repo's own type named e.g. `Repository`
        conf = _origin(base, imports, _SPRING_DATA)
        if conf is None:
            continue
        cls.attributes["repository"] = {
            "base": simple,
            "entity": args[0],
            "id": args[1] if len(args) > 1 else None,
        }
        found = resolver.resolve_type(args[0], cls.id)
        if found.class_id is not None and found.class_id in entity_of:
            conf = _weakest(conf, found.confidence)
            out.edges.append(_edge(EdgeKind.STORES, cls.id, entity_of[found.class_id], conf, cls))
        return
