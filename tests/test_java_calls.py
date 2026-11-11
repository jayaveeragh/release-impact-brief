from pathlib import Path
from typing import Any

import pytest

from release_impact_brief.adapters import Extraction, extract_all
from release_impact_brief.adapters.java import JavaAdapter
from release_impact_brief.facts import Confidence

FILES = {
    "shop/model/BaseEntity.java": """\
package shop.model;

public class BaseEntity {
    protected Integer id;
    public Integer getId() { return id; }
}
""",
    "shop/model/Owner.java": """\
package shop.model;

import java.util.List;

public class Owner extends BaseEntity {
    private String name;
    private List<Pet> pets;

    public Owner() {}
    public Owner(String name) { this.name = name; }

    public String getName() { return name.trim(); }
    public Pet getPet(String name) { return pets.get(0); }
    public Pet getPet(Integer id) { return null; }
    public void rename(String name) { this.name = name; validate(); }
    private void validate() { getId(); }
}
""",
    "shop/model/Pet.java": """\
package shop.model;

public class Pet {
    public void addVisit(String visit) {}
}
""",
    "shop/repo/OwnerRepository.java": """\
package shop.repo;

import shop.model.Owner;

public interface OwnerRepository extends JpaRepository<Owner, Integer> {
    Owner findByName(String name);
}
""",
    "shop/service/Notifier.java": """\
package shop.service;

public interface Notifier {
    void send(String message);
}
""",
    "shop/service/EmailNotifier.java": """\
package shop.service;

@Service
public class EmailNotifier implements Notifier {
    @Override
    public void send(String message) {}
}
""",
    "shop/web/OwnerController.java": """\
package shop.web;

import shop.model.Owner;
import shop.model.Pet;
import shop.repo.OwnerRepository;
import shop.service.Notifier;
import static shop.web.Paths.base;

@Controller
public class OwnerController {
    private final OwnerRepository owners;

    @Autowired
    private Notifier notifier;

    public OwnerController(OwnerRepository owners) {
        this.owners = owners;
    }

    public String show(String name, Model model) {
        Owner owner = owners.findByName(name);
        owner.getPet(name).addVisit("today");
        this.owners.save(owner);
        model.addAttribute("owner", owner);
        notifier.send(owner.getName());
        Owner fresh = new Owner("x");
        mystery.handleUnique();
        mystery.unknownThing();
        base();
        return Paths.base();
    }
}
""",
    "shop/web/Paths.java": """\
package shop.web;

public class Paths {
    public static String base() { return "/"; }
}
""",
    "shop/web/Unique.java": """\
package shop.web;

class Unique {
    void handleUnique() {}
}
""",
    "shop/web/LombokController.java": """\
package shop.web;

import shop.repo.OwnerRepository;

@RestController
@RequiredArgsConstructor
public class LombokController {
    private final OwnerRepository owners;
    private static final String NAME = "x";
}
""",
}

P = "java:method:"
SHOW = f"{P}shop.web.OwnerController#show(String,Model)"


@pytest.fixture(scope="module")
def facts(tmp_path_factory: pytest.TempPathFactory) -> Extraction:
    root = tmp_path_factory.mktemp("shop")
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return extract_all(root, [JavaAdapter()])


def edges(facts: Extraction, kind: str) -> dict[tuple[str, str], Confidence]:
    return {(e.src, e.dst): e.confidence for e in facts.edges if e.kind == kind}


def attrs(facts: Extraction, node_id: str) -> dict[str, Any]:
    return next(n.attributes for n in facts.nodes if n.id == node_id)


def test_call_on_field_typed_by_repo_interface_is_exact(facts: Extraction) -> None:
    calls = edges(facts, "calls")
    assert calls[(SHOW, f"{P}shop.repo.OwnerRepository#findByName(String)")] == Confidence.EXACT


def test_call_on_local_variable_and_chained_return_type(facts: Extraction) -> None:
    calls = edges(facts, "calls")
    assert calls[(SHOW, f"{P}shop.model.Owner#getName()")] == Confidence.EXACT
    # getPet(name) is an ambiguous overload, but both return Pet: kept, at "likely".
    assert calls[(SHOW, f"{P}shop.model.Pet#addVisit(String)")] == Confidence.LIKELY


def test_overloads_with_same_arity_are_likely_not_exact(facts: Extraction) -> None:
    calls = edges(facts, "calls")
    assert calls[(SHOW, f"{P}shop.model.Owner#getPet(String)")] == Confidence.LIKELY
    assert calls[(SHOW, f"{P}shop.model.Owner#getPet(Integer)")] == Confidence.LIKELY


def test_implicit_call_to_own_and_inherited_method(facts: Extraction) -> None:
    calls = edges(facts, "calls")
    rename = f"{P}shop.model.Owner#rename(String)"
    validate = f"{P}shop.model.Owner#validate()"
    assert calls[(rename, validate)] == Confidence.EXACT
    assert calls[(validate, f"{P}shop.model.BaseEntity#getId()")] == Confidence.EXACT


def test_constructor_call_picks_overload_by_arity(facts: Extraction) -> None:
    calls = edges(facts, "calls")
    assert calls[(SHOW, f"{P}shop.model.Owner#Owner(String)")] == Confidence.EXACT
    assert (SHOW, f"{P}shop.model.Owner#Owner()") not in calls


def test_static_calls_by_class_name_and_static_import(facts: Extraction) -> None:
    assert edges(facts, "calls")[(SHOW, f"{P}shop.web.Paths#base()")] == Confidence.EXACT


def test_spring_data_method_points_to_repo_interface_and_is_labelled(facts: Extraction) -> None:
    calls = edges(facts, "calls")
    assert calls[(SHOW, "java:class:shop.repo.OwnerRepository")] == Confidence.LIKELY
    assert (
        "OwnerRepository.save (inherited from JpaRepository)" in attrs(facts, SHOW)["library_calls"]
    )


def test_library_calls_are_listed_not_traced(facts: Extraction) -> None:
    assert "Model.addAttribute" in attrs(facts, SHOW)["library_calls"]
    get_name = attrs(facts, f"{P}shop.model.Owner#getName()")
    assert get_name["library_calls"] == ["String.trim"]
    get_pet = attrs(facts, f"{P}shop.model.Owner#getPet(String)")
    assert get_pet["library_calls"] == ["List.get"]
    library_targets = [d for (s, d) in edges(facts, "calls") if "String" in d.split("#")[0]]
    assert library_targets == []


def test_unknown_receiver_unique_name_is_a_guess_else_unresolved(facts: Extraction) -> None:
    calls = edges(facts, "calls")
    assert calls[(SHOW, f"{P}shop.web.Unique#handleUnique()")] == Confidence.GUESS
    assert attrs(facts, SHOW)["unresolved_calls"] == ["?.unknownThing"]


def test_extends_and_implements_edges(facts: Extraction) -> None:
    assert (
        edges(facts, "extends")[("java:class:shop.model.Owner", "java:class:shop.model.BaseEntity")]
        == Confidence.EXACT
    )
    assert (
        edges(facts, "implements")[
            ("java:class:shop.service.EmailNotifier", "java:class:shop.service.Notifier")
        ]
        == Confidence.EXACT
    )
    # JpaRepository isn't in the repo, so no edge for it.
    assert not any(s == "java:class:shop.repo.OwnerRepository" for s, _ in edges(facts, "extends"))


def test_implementation_overrides_interface_method(facts: Extraction) -> None:
    assert (
        edges(facts, "overrides")[
            (
                f"{P}shop.service.EmailNotifier#send(String)",
                f"{P}shop.service.Notifier#send(String)",
            )
        ]
        == Confidence.EXACT
    )


def test_spring_injection(facts: Extraction) -> None:
    injects = edges(facts, "injects")
    controller = "java:class:shop.web.OwnerController"
    assert injects[(controller, "java:class:shop.repo.OwnerRepository")] == Confidence.EXACT
    assert injects[(controller, "java:class:shop.service.Notifier")] == Confidence.EXACT
    lombok = "java:class:shop.web.LombokController"
    assert injects[(lombok, "java:class:shop.repo.OwnerRepository")] == Confidence.LIKELY
    assert len([s for s, _ in injects if s == lombok]) == 1


def test_no_injection_without_spring_annotation(tmp_path: Path) -> None:
    (tmp_path / "A.java").write_text("class Dep {}\nclass A { A(Dep d) {} }\n")
    assert edges(extract_all(tmp_path, [JavaAdapter()]), "injects") == {}


def test_every_call_edge_points_to_a_real_node(facts: Extraction) -> None:
    ids = {n.id for n in facts.nodes}
    assert all(e.src in ids and e.dst in ids for e in facts.edges)


LOMBOK = {
    "Pet.java": """\
@Data
class Pet {
    private String name;
    private boolean vaccinated;
    private Owner owner;
}
""",
    "Owner.java": """\
@Value
class Owner {
    String city;
    public String label() { return city; }
}
""",
    "Tag.java": """\
class Tag {
    @Getter private String code;
    @Setter private String note;
}
""",
    "Clinic.java": """\
class Clinic {
    void run(Pet pet, Owner owner, Tag tag) {
        pet.getName();
        pet.isVaccinated();
        pet.setName("x");
        pet.getOwner().label();
        owner.getCity();
        owner.setCity("y");
        tag.getCode();
        tag.setNote("z");
        tag.getNote();
    }
}
""",
}


def test_lombok_generated_accessors_are_marked_not_guessed(tmp_path: Path) -> None:
    for rel, text in LOMBOK.items():
        (tmp_path / rel).write_text(text)
    facts = extract_all(tmp_path, [JavaAdapter()])
    run = f"{P}Clinic#run(Pet,Owner,Tag)"
    assert sorted(attrs(facts, run)["library_calls"]) == [
        "Owner.getCity (generated by Lombok @Value)",
        "Pet.getName (generated by Lombok @Data)",
        "Pet.getOwner (generated by Lombok @Data)",
        "Pet.isVaccinated (generated by Lombok @Data)",
        "Pet.setName (generated by Lombok @Data)",
        "Tag.getCode (generated by Lombok @Getter)",
        "Tag.setNote (generated by Lombok @Setter)",
    ]
    # @Value has no setters; a field-level @Setter gives no getter.
    assert sorted(attrs(facts, run)["unresolved_calls"]) == ["Owner.setCity", "Tag.getNote"]
    calls = edges(facts, "calls")
    assert calls[(run, "java:class:Pet")] == Confidence.LIKELY
    # The generated getter's return type keeps the chain going.
    assert calls[(run, f"{P}Owner#label()")] == Confidence.LIKELY
