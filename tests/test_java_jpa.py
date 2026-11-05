import json
from pathlib import Path

import pytest

from release_impact_brief.adapters import Extraction, extract_all
from release_impact_brief.adapters.java import JavaAdapter
from release_impact_brief.facts import Confidence, EdgeKind, NodeKind

FILES = {
    "shop/model/BaseEntity.java": """\
package shop.model;

import jakarta.persistence.*;

@MappedSuperclass
public class BaseEntity {
    @Id @GeneratedValue private Integer id;
}
""",
    "shop/model/Customer.java": """\
package shop.model;

import jakarta.persistence.CascadeType;
import jakarta.persistence.Embedded;
import jakarta.persistence.Entity;
import jakarta.persistence.OneToMany;
import jakarta.persistence.Table;
import java.util.List;

@Entity
@Table(name = "customers")
public class Customer extends BaseEntity {
    @OneToMany(mappedBy = "customer", cascade = CascadeType.ALL)
    private List<Order> orders;

    @Embedded private Address address;
}
""",
    "shop/model/Order.java": """\
package shop.model;

import jakarta.persistence.*;
import java.util.Map;
import java.util.Set;

@Entity(name = "PurchaseOrder")
public class Order extends BaseEntity {
    @ManyToOne private Customer customer;
    @ManyToMany(targetEntity = Tag.class) private Set tags;
    @OneToMany private Map<String, Customer> byCode;
}
""",
    "shop/model/Tag.java": """\
package shop.model;

import javax.persistence.Entity;

@Entity
public class Tag {}
""",
    "shop/model/Address.java": """\
package shop.model;

import jakarta.persistence.Embeddable;

@Embeddable
public class Address { private String city; }
""",
    "shop/docs/Note.java": """\
package shop.docs;

import dev.morphia.annotations.Entity;

@Entity
public class Note {}
""",
    "shop/legacy/Thing.java": """\
package shop.legacy;

@Entity
public class Thing {}
""",
    "shop/repo/CustomerRepository.java": """\
package shop.repo;

import org.springframework.data.jpa.repository.JpaRepository;
import shop.model.Customer;

public interface CustomerRepository extends JpaRepository<Customer, Integer> {}
""",
    "shop/repo/OrderRepository.java": """\
package shop.repo;

import org.springframework.data.jpa.domain.JpaSpecificationExecutor;
import org.springframework.data.repository.CrudRepository;
import shop.model.Order;

public interface OrderRepository
        extends JpaSpecificationExecutor<Order>, CrudRepository<Order, Long> {}
""",
    "shop/repo/Sorter.java": """\
package shop.repo;

import java.util.Comparator;
import shop.model.Customer;

public interface Sorter extends Comparator<Customer> {}
""",
}

M = "java:class:shop.model."


@pytest.fixture(scope="module")
def facts(tmp_path_factory: pytest.TempPathFactory) -> Extraction:
    root = tmp_path_factory.mktemp("shop")
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return extract_all(root, [JavaAdapter()])


def edges(facts: Extraction, kind: EdgeKind) -> dict[tuple[str, str], Confidence]:
    return {(e.src, e.dst): e.confidence for e in facts.edges if e.kind == kind}


def test_entities_and_table_names(facts: Extraction) -> None:
    entities = {
        n.id: (n.name, n.attributes["table_source"])
        for n in facts.nodes
        if n.kind == NodeKind.ENTITY
    }
    assert entities == {
        "java:entity:shop.model.Customer": ("customers", "@Table"),
        "java:entity:shop.model.Order": ("PurchaseOrder", "@Entity(name)"),
        "java:entity:shop.model.Tag": ("Tag", "default"),
        "java:entity:shop.legacy.Thing": ("Thing", "default"),
    }


def test_maps_edge_confidence_depends_on_the_import(facts: Extraction) -> None:
    maps = edges(facts, EdgeKind.MAPS)
    assert maps[(f"{M}Customer", "java:entity:shop.model.Customer")] == Confidence.EXACT
    assert maps[(f"{M}Order", "java:entity:shop.model.Order")] == Confidence.EXACT  # wildcard
    assert maps[(f"{M}Tag", "java:entity:shop.model.Tag")] == Confidence.EXACT  # javax
    # No import at all: probably JPA, but we can't see which @Entity it is.
    assert maps[("java:class:shop.legacy.Thing", "java:entity:shop.legacy.Thing")] == (
        Confidence.LIKELY
    )
    # Morphia's @Entity is not JPA.
    assert not any("Note" in src for src, _ in maps)


def test_class_roles(facts: Extraction) -> None:
    roles = {n.name: n.attributes.get("jpa") for n in facts.nodes if n.kind == NodeKind.CLASS}
    assert roles["BaseEntity"] == "mapped_superclass"
    assert roles["Address"] == "embeddable"
    assert roles["Customer"] == "entity"
    assert roles["Note"] is None


def test_relations_link_field_to_target_entity(facts: Extraction) -> None:
    relates = edges(facts, EdgeKind.RELATES)
    f = "java:field:shop.model."
    assert relates == {
        (f"{f}Customer#orders", "java:entity:shop.model.Order"): Confidence.EXACT,
        (f"{f}Order#customer", "java:entity:shop.model.Customer"): Confidence.EXACT,
        (f"{f}Order#tags", "java:entity:shop.model.Tag"): Confidence.EXACT,  # targetEntity
        (f"{f}Order#byCode", "java:entity:shop.model.Customer"): Confidence.EXACT,  # Map value
    }
    fields = {n.id: n.attributes for n in facts.nodes if n.kind == NodeKind.FIELD}
    orders = fields[f"{f}Customer#orders"]
    assert orders["relation"] == {"kind": "OneToMany", "target": "Order"}
    assert orders["annotation_args"]["OneToMany"]["mappedBy"] == ['"customer"']
    assert "relation" not in fields[f"{f}Customer#address"]  # @Embedded is not a relation


def test_repositories_link_to_their_entity(facts: Extraction) -> None:
    stores = edges(facts, EdgeKind.STORES)
    r = "java:class:shop.repo."
    assert stores == {
        (f"{r}CustomerRepository", "java:entity:shop.model.Customer"): Confidence.EXACT,
        (f"{r}OrderRepository", "java:entity:shop.model.Order"): Confidence.EXACT,
    }
    classes = {n.name: n.attributes for n in facts.nodes if n.kind == NodeKind.CLASS}
    assert classes["OrderRepository"]["repository"] == {
        "base": "CrudRepository",
        "entity": "Order",
        "id": "Long",
    }
    assert "repository" not in classes["Sorter"]


FIXTURE = Path(__file__).parent / "fixtures" / "spring-petclinic" / "expected.json"


@pytest.mark.network
def test_petclinic_data_model_matches_hand_written_answer_key() -> None:
    from release_impact_brief.fetch import fetch_repo

    key = json.loads(FIXTURE.read_text())
    with fetch_repo(key["repo"], key["commit_sha"]) as fetched:
        result = extract_all(fetched.path, [JavaAdapter()])
    prefix = f"{key['package']}."

    def short(node_id: str) -> str:
        return node_id.split(":", 2)[2].removeprefix(prefix)

    nodes = {n.id: n for n in result.nodes}
    supers = {e.src: e.dst for e in result.edges if e.kind == EdgeKind.EXTENDS}
    entities = [n for n in result.nodes if n.kind == NodeKind.ENTITY]
    assert {
        (short(n.attributes["class"]), n.name, short(supers[n.attributes["class"]]))
        for n in entities
    } == {(e["class"], e["table"], e["extends"]) for e in key["entities"]}

    mapped = {
        short(n.id)
        for n in result.nodes
        if n.kind == NodeKind.CLASS and n.attributes.get("jpa") == "mapped_superclass"
    }
    assert mapped == set(key["mapped_superclasses"])

    owner = {e.dst: e.src for e in result.edges if e.kind == EdgeKind.CONTAINS}
    relations = {
        (short(owner[e.src]), nodes[e.src].name, nodes[e.src].attributes["relation"]["kind"],
         short(e.dst))
        for e in result.edges
        if e.kind == EdgeKind.RELATES
    }  # fmt: skip
    assert relations == {(r["from"], r["field"], r["kind"], r["to"]) for r in key["relations"]}

    repositories = {
        (short(e.src), short(e.dst), nodes[e.src].attributes["repository"]["base"])
        for e in result.edges
        if e.kind == EdgeKind.STORES
    }
    assert repositories == {
        (r["interface"], r["entity"], r["extends"]) for r in key["repositories"]
    }
    assert all(
        e.confidence == Confidence.EXACT
        for e in result.edges
        if e.kind in (EdgeKind.MAPS, EdgeKind.RELATES, EdgeKind.STORES)
    )
