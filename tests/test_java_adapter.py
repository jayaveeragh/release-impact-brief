from pathlib import Path

import pytest

from release_impact_brief.adapters import Extraction, extract_all
from release_impact_brief.adapters.java import JavaAdapter
from release_impact_brief.facts import FactStore, Node, NodeKind

OWNER = """\
package org.example.owner;

import java.util.List;
import static java.util.Objects.requireNonNull;

@Entity
@Table(name = "owners")
public class Owner extends Person implements Serializable, Comparable<Owner> {

    @OneToMany
    private final List<Pet> pets = new ArrayList<>();

    private String city, telephone;

    public Owner() {
    }

    public Pet getPet(String name) {
        return null;
    }

    public Pet getPet(Integer id, boolean ignoreNew) {
        return null;
    }

    public void addAll(@NotNull String... names) {
    }

    static class Address {
        String street;
    }
}
"""

TYPES = """\
package org.example.owner;

public interface OwnerRepository extends JpaRepository<Owner, Integer> {
    Owner findById(@Param("id") int id);
}

enum Status {
    ACTIVE, INACTIVE;
    private int code;
}

record Point(int x, @NotNull String label) {
}
"""


@pytest.fixture
def facts(tmp_path: Path) -> Extraction:
    pkg = tmp_path / "src" / "org" / "example" / "owner"
    pkg.mkdir(parents=True)
    (pkg / "Owner.java").write_text(OWNER)
    (pkg / "Types.java").write_text(TYPES)
    (tmp_path / "README.md").write_text("not java")
    return extract_all(tmp_path, [JavaAdapter()])


def by_id(facts: Extraction) -> dict[str, Node]:
    return {n.id: n for n in facts.nodes}


def test_parses_only_java_files(facts: Extraction) -> None:
    assert facts.files_parsed == 2
    assert facts.files_with_errors == []


def test_class_with_package_annotations_and_supertypes(facts: Extraction) -> None:
    owner = by_id(facts)["java:class:org.example.owner.Owner"]
    assert owner.kind == NodeKind.CLASS
    assert owner.file == "src/org/example/owner/Owner.java"
    assert (owner.start_line, owner.end_line) == (6, 32)
    assert owner.attributes["annotations"] == ["Entity", "Table"]
    assert owner.attributes["extends"] == ["Person"]
    assert owner.attributes["implements"] == ["Serializable", "Comparable<Owner>"]
    assert owner.attributes["imports"] == [
        "java.util.List",
        "static java.util.Objects.requireNonNull",
    ]


def test_overloaded_methods_get_distinct_ids(facts: Extraction) -> None:
    nodes = by_id(facts)
    one = nodes["java:method:org.example.owner.Owner#getPet(String)"]
    two = nodes["java:method:org.example.owner.Owner#getPet(Integer,boolean)"]
    assert (one.start_line, two.start_line) == (18, 22)
    assert one.attributes["return_type"] == "Pet"
    assert two.attributes["parameters"][1] == {
        "name": "ignoreNew",
        "type": "boolean",
        "annotations": [],
    }


def test_constructor_and_varargs(facts: Extraction) -> None:
    nodes = by_id(facts)
    ctor = nodes["java:method:org.example.owner.Owner#Owner()"]
    assert ctor.attributes["constructor"] is True
    assert ctor.attributes["return_type"] is None
    varargs = nodes["java:method:org.example.owner.Owner#addAll(String...)"]
    assert varargs.attributes["parameters"] == [
        {"name": "names", "type": "String...", "annotations": ["NotNull"]}
    ]


def test_fields_including_multi_declarators(facts: Extraction) -> None:
    nodes = by_id(facts)
    pets = nodes["java:field:org.example.owner.Owner#pets"]
    assert pets.attributes == {
        "type": "List<Pet>",
        "modifiers": ["private", "final"],
        "annotations": ["OneToMany"],
        "relation": {"kind": "OneToMany", "target": "Pet"},
    }
    assert nodes["java:field:org.example.owner.Owner#city"].attributes["type"] == "String"
    assert nodes["java:field:org.example.owner.Owner#telephone"].start_line == 13


def test_nested_class_is_contained_by_outer(facts: Extraction) -> None:
    inner = "java:class:org.example.owner.Owner.Address"
    assert inner in by_id(facts)
    contains = {(e.src, e.dst) for e in facts.edges if e.kind == "contains"}
    assert ("java:class:org.example.owner.Owner", inner) in contains
    assert (inner, "java:field:org.example.owner.Owner.Address#street") in contains


def test_interface_enum_and_record(facts: Extraction) -> None:
    nodes = by_id(facts)
    repo = nodes["java:class:org.example.owner.OwnerRepository"]
    assert repo.attributes["type_kind"] == "interface"
    assert repo.attributes["extends"] == ["JpaRepository<Owner,Integer>"]
    assert "java:method:org.example.owner.OwnerRepository#findById(int)" in nodes

    assert nodes["java:class:org.example.owner.Status"].attributes["type_kind"] == "enum"
    assert nodes["java:field:org.example.owner.Status#ACTIVE"].attributes["enum_constant"] is True
    assert "java:field:org.example.owner.Status#code" in nodes

    label = nodes["java:field:org.example.owner.Point#label"]
    assert label.attributes["record_component"] is True
    assert label.attributes["annotations"] == ["NotNull"]


def test_every_member_has_exactly_one_container(facts: Extraction) -> None:
    contained = [e.dst for e in facts.edges if e.kind == "contains"]
    top_level = {
        n.id for n in facts.nodes if n.kind == NodeKind.CLASS and "imports" in n.attributes
    }
    members = (NodeKind.CLASS, NodeKind.METHOD, NodeKind.FIELD)  # entities hang off `maps`
    assert sorted(contained) == sorted(
        n.id for n in facts.nodes if n.kind in members and n.id not in top_level
    )


def test_same_qualified_name_in_two_files_keeps_both(tmp_path: Path) -> None:
    for module in ("a", "b"):
        (tmp_path / module).mkdir()
        (tmp_path / module / "App.java").write_text("package demo;\nclass App {}\n")
    ids = [n.id for n in extract_all(tmp_path, [JavaAdapter()]).nodes]
    assert ids == ["java:class:demo.App", "java:class:demo.App@b/App.java"]


def test_syntax_errors_are_reported_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "Broken.java").write_text("class Broken { void m( { }\n")
    result = extract_all(tmp_path, [JavaAdapter()])
    assert result.files_with_errors == ["Broken.java"]


def test_skipped_files_are_not_parsed(tmp_path: Path) -> None:
    (tmp_path / "Big.java").write_text("class Big {}\n")
    assert extract_all(tmp_path, [JavaAdapter()], skip=["Big.java"]).files_parsed == 0


def test_extraction_round_trips_through_fact_store(facts: Extraction) -> None:
    store = FactStore(":memory:")
    snap = store.snapshot("github.com/example/demo", "c" * 40)
    store.add_facts(snap, facts.nodes, facts.edges)
    assert len(store.nodes(snap)) == len(facts.nodes)
    assert len(store.edges(snap)) == len(facts.edges)
    store.close()


@pytest.mark.network
def test_whole_petclinic_parses_without_crashing() -> None:
    # Regression guard: tree-sitter 0.26.0 segfaulted on petclinic's test classes.
    from release_impact_brief.fetch import fetch_repo

    sha = "500158f732419217507c7656904b8e6aa1bcc0d6"
    with fetch_repo("https://github.com/spring-projects/spring-petclinic", sha) as fetched:
        result = extract_all(fetched.path, [JavaAdapter()])
    assert result.files_parsed == 50
    assert result.files_with_errors == []
