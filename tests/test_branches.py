import json
from pathlib import Path
from typing import Any

import pytest

from release_impact_brief.adapters import Extraction
from release_impact_brief.branches import branch_map
from release_impact_brief.pipeline import analyze

FILES = {
    "pom.xml": "<project><groupId>s</groupId><artifactId>shop</artifactId></project>",
    "src/main/java/shop/ShopApp.java": "package shop;\n@SpringBootApplication\nclass ShopApp {}\n",
    "src/main/java/shop/Named.java": """\
package shop;

class Named {
    @NotBlank
    @Size(max = 30)
    private String name;

    public String getName() { return name; }
    public boolean isNew() { return true; }
}
""",
    "src/main/java/shop/Item.java": """\
package shop;

class Item extends Named {
    private LocalDate madeOn;
    @Pattern(regexp = "\\\\d+")
    private String code;
    private String note;

    public LocalDate getMadeOn() { return madeOn; }
    public String getCode() { return code; }
}
""",
    "src/main/java/shop/ItemValidator.java": "package shop;\nclass ItemValidator {}\n",
    "src/main/java/shop/ItemController.java": """\
package shop;

@Controller
class ItemController {
    private final ItemRepository items;

    @InitBinder("item")
    public void binder(WebDataBinder b) { b.setValidator(new ItemValidator()); }

    @PostMapping("/items/new")
    public String create(@Valid Item item, BindingResult result, RedirectAttributes redirect) {
        if (StringUtils.hasText(item.getName()) && item.isNew() && lookup(item.getName()) != null) {
            result.rejectValue("name", "duplicate");
        }
        if (item.getMadeOn() != null && item.getMadeOn().isAfter(LocalDate.now())) {
            result.rejectValue("madeOn", "future");
        }
        if (result.hasErrors()) {
            return "items/form";
        }
        try {
            items.save(item);
        }
        catch (DataException ex) {
            if (!duplicate(ex)) {
                throw ex;
            }
            return "items/form";
        }
        return "redirect:/items";
    }

    @GetMapping("/items")
    public String find(@RequestParam(defaultValue = "1") int page, Item item, Model model) {
        String code = item.getCode();
        if (code == null) {
            code = "";
        }
        Page<Item> found = search(page, code);
        if (found.isEmpty()) {
            return "items/none";
        }
        return "items/list";
    }
}
""",
}


@pytest.fixture(scope="module")
def facts(tmp_path_factory: pytest.TempPathFactory) -> Extraction:
    root = tmp_path_factory.mktemp("shop")
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return analyze(root)


def by_line(result: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {b["line"]: b for b in result["branches"]}


def test_drivers_follow_getters_locals_and_validation(facts: Extraction) -> None:
    result = branch_map(facts.nodes, facts.edges, "java:endpoint:POST /items/new")
    lines = by_line(result)
    assert lines[12]["drivers"] == ["item.isNew()", "item.name"]
    assert lines[15]["drivers"] == ["item.madeOn"]
    # hasErrors = the @Valid check plus every earlier rejectValue's conditions
    assert lines[18]["drivers"] == ["item.isNew()", "item.madeOn", "item.name",
                                    "validation:item"]  # fmt: skip
    assert (lines[24]["kind"], lines[24]["drivers"], lines[24]["exits"]) == ("catch", [], [26, 28])
    assert lines[25]["drivers"] == []  # the exception, not a request input
    assert result["returns"] == [19, 28, 30] and result["throws"] == [26]


def test_validation_and_custom_validator(facts: Extraction) -> None:
    result = branch_map(facts.nodes, facts.edges, "java:endpoint:POST /items/new")
    assert result["validation"] == {"item": ["code", "name"]}  # annotated, incl. inherited
    assert result["custom_validator"] == "ItemValidator"


def test_parameter_table_separates_drivers_from_pass_through(facts: Extraction) -> None:
    result = branch_map(facts.nodes, facts.edges, "java:endpoint:POST /items/new")
    roles = {row["name"]: row["role"] for row in result["inputs"]}
    assert roles["item.name"] == "drives"
    assert roles["item.madeOn"] == "drives"
    assert roles["item.code"] == "drives"  # through validation (@Pattern)
    assert roles["item.note"] == "passed through"  # item goes to items.save(item)
    code = next(r for r in result["inputs"] if r["name"] == "item.code")
    assert code["constraints"] == ["Pattern"]
    assert "redirect" not in roles  # framework parameter, not a request input

    found = branch_map(facts.nodes, facts.edges, "java:endpoint:GET /items")
    roles = {row["name"]: row["role"] for row in found["inputs"]}
    assert roles["page"] == "drives" and roles["item.code"] == "drives"
    assert roles["item.note"] == "unused" and roles["item.name"] == "unused"
    assert by_line(found)[40]["drivers"] == ["item.code", "page"]  # via the search result


def test_fields_of_an_object_read_as_a_whole_are_not_claimed_as_drivers(
    facts: Extraction,
) -> None:
    from release_impact_brief.facts import Node, NodeKind

    # Make the handler call a method with arguments on `item` itself, as petclinic's
    # owner.getPet(name, true) does.
    nodes = []
    for n in facts.nodes:
        if n.kind == NodeKind.METHOD and n.name == "create":
            flow = dict(n.attributes["flow"])
            flow["branches"] = [dict(flow["branches"][0], reads=["item.matches(...)"])]
            n = Node(n.id, n.kind, n.name, n.file, n.start_line, n.end_line, n.language,
                     n.attributes | {"flow": flow})  # fmt: skip
        nodes.append(n)
    result = branch_map(nodes, facts.edges, "java:endpoint:POST /items/new")
    roles = {row["name"]: row["role"] for row in result["inputs"]}
    assert roles["item"] == "drives"
    assert roles["item.note"] == "read as a whole"
    assert roles["item.code"] == "read as a whole"  # no validation check in this variant


def test_branches_list_what_each_path_calls(facts: Extraction) -> None:
    result = branch_map(facts.nodes, facts.edges, "java:endpoint:POST /items/new")
    assert any("lookup" in c for c in by_line(result)[12]["calls"])


def test_unknown_endpoint(facts: Extraction) -> None:
    with pytest.raises(KeyError):
        branch_map(facts.nodes, facts.edges, "java:endpoint:GET /nope")


FIXTURE = Path(__file__).parent / "fixtures" / "spring-petclinic" / "expected.json"


@pytest.mark.network
def test_petclinic_branch_maps_match_hand_written_answer_key() -> None:
    from release_impact_brief.fetch import fetch_repo

    key = json.loads(FIXTURE.read_text())
    with fetch_repo(key["repo"], key["commit_sha"]) as fetched:
        facts = analyze(fetched.path)
    for expected in key["branches"]["endpoints"]:
        result = branch_map(facts.nodes, facts.edges, f"java:endpoint:{expected['endpoint']}")
        found = [(b["line"], b["kind"], b["drivers"], b["exits"]) for b in result["branches"]]
        wanted = [(b["line"], b["kind"], sorted(b["drivers"]), b["exits_when_true"])
                  for b in expected["branches"]]  # fmt: skip
        assert found == wanted, expected["endpoint"]
        assert (result["returns"], result["throws"]) == (expected["returns"], expected["throws"])
        assert {k: sorted(v) for k, v in result["validation"].items()} == {
            k: sorted(v) for k, v in expected["validation"].items()}  # fmt: skip
        roles = {row["name"]: row["role"] for row in result["inputs"]}
        assert {name: roles.get(name) for name in expected["inputs"]} == expected["inputs"]
        if "custom_validator" in expected:
            assert result["custom_validator"] == "PetValidator"
