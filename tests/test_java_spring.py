import json
from pathlib import Path
from typing import Any

import pytest

from release_impact_brief.adapters import Extraction, extract_all
from release_impact_brief.adapters.java import JavaAdapter
from release_impact_brief.facts import Confidence, NodeKind

FILES = {
    "web/OwnerController.java": """\
package web;

@Controller
@RequestMapping("/owners/{ownerId}")
class OwnerController {

    @GetMapping("/pets/new")
    public String initForm(Owner owner, ModelMap model) { return "form"; }

    @PostMapping("/pets/{petId}/edit")
    public String update(@Valid Pet pet, BindingResult result, @PathVariable("petId") int id,
            RedirectAttributes redirect) { return "redirect:"; }

    @GetMapping
    public ModelAndView show(@PathVariable(name = "ownerId", required = false) Integer ownerId) {
        return null;
    }

    @GetMapping({ "/vets", "/vets.json" })
    public @ResponseBody Vets list(@RequestParam(defaultValue = "1") int page, String sort) {
        return null;
    }

    @ModelAttribute("owner")
    public Owner findOwner() { return null; }

    @InitBinder
    public void binder(WebDataBinder b) {}
}
""",
    "web/ApiController.java": """\
package web;

@RestController
@RequestMapping(path = "/api/")
class ApiController {

    @RequestMapping(value = "/items", method = { RequestMethod.GET, RequestMethod.HEAD })
    public List<Item> items() { return null; }

    @PutMapping("/items/{id}")
    public ResponseEntity<Item> put(@PathVariable String id, @RequestBody @Validated Item item,
            @RequestHeader("X-Trace") String trace) { return null; }

    @RequestMapping("/any")
    public void any() {}

    @DeleteMapping(Paths.ITEM)
    public void delete() {}
}
""",
    "web/NotAController.java": """\
package web;

@Service
class NotAController {
    @GetMapping("/ignored")
    public String x() { return ""; }
}
""",
}


@pytest.fixture(scope="module")
def facts(tmp_path_factory: pytest.TempPathFactory) -> Extraction:
    root = tmp_path_factory.mktemp("web")
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return extract_all(root, [JavaAdapter()])


def endpoints(facts: Extraction) -> dict[str, dict[str, Any]]:
    return {n.name: n.attributes for n in facts.nodes if n.kind == NodeKind.ENDPOINT}


def test_endpoint_paths_join_class_prefix(facts: Extraction) -> None:
    assert sorted(endpoints(facts)) == [
        "ANY /api/any",
        "DELETE /api/<Paths.ITEM>",
        "GET /api/items",
        "GET /owners/{ownerId}",
        "GET /owners/{ownerId}/pets/new",
        "GET /owners/{ownerId}/vets",
        "GET /owners/{ownerId}/vets.json",
        "HEAD /api/items",
        "POST /owners/{ownerId}/pets/{petId}/edit",
        "PUT /api/items/{id}",
    ]


def test_handles_edge_links_endpoint_to_handler(facts: Extraction) -> None:
    handles = {(e.src, e.dst): e.confidence for e in facts.edges if e.kind == "handles"}
    key = (
        "java:endpoint:POST /owners/{ownerId}/pets/{petId}/edit",
        "java:method:web.OwnerController#update(Pet,BindingResult,int,RedirectAttributes)",
    )
    assert handles[key] == Confidence.EXACT


def test_unresolved_constant_path_is_marked_guess(facts: Extraction) -> None:
    attrs = endpoints(facts)["DELETE /api/<Paths.ITEM>"]
    assert attrs["path_resolved"] is False
    handles = [e for e in facts.edges if e.kind == "handles" and "Paths.ITEM" in e.src]
    assert [e.confidence for e in handles] == [Confidence.GUESS]


def test_inputs_classified_by_source(facts: Extraction) -> None:
    update = endpoints(facts)["POST /owners/{ownerId}/pets/{petId}/edit"]["inputs"]
    assert update == [
        {"name": "pet", "type": "Pet", "source": "form", "implicit": True, "validated": True},
        {"name": "petId", "type": "int", "source": "path"},
    ]
    put = endpoints(facts)["PUT /api/items/{id}"]["inputs"]
    assert put == [
        {"name": "id", "type": "String", "source": "path"},
        {"name": "item", "type": "Item", "source": "body", "validated": True},
        {"name": "X-Trace", "type": "String", "source": "header"},
    ]


def test_request_param_defaults_required_and_implicit_simple_params(facts: Extraction) -> None:
    inputs = endpoints(facts)["GET /owners/{ownerId}/vets"]["inputs"]
    assert inputs == [
        {"name": "page", "type": "int", "source": "query", "default": "1"},
        {"name": "sort", "type": "String", "source": "query", "implicit": True},
    ]
    show = endpoints(facts)["GET /owners/{ownerId}"]["inputs"]
    assert show == [{"name": "ownerId", "type": "Integer", "source": "path", "required": False}]


def test_response_kind_view_or_body(facts: Extraction) -> None:
    eps = endpoints(facts)
    assert eps["GET /owners/{ownerId}/pets/new"]["response"] == "view"
    assert eps["GET /owners/{ownerId}/vets"]["response"] == "body"  # @ResponseBody
    assert eps["GET /api/items"]["response"] == "body"  # @RestController
    assert eps["PUT /api/items/{id}"]["returns"] == "ResponseEntity<Item>"


def test_only_controllers_and_mapped_methods_become_endpoints(facts: Extraction) -> None:
    handlers = {a["handler"] for a in endpoints(facts).values()}
    assert not any("NotAController" in h for h in handlers)
    assert not any("findOwner" in h or "binder" in h for h in handlers)


FIXTURE = Path(__file__).parent / "fixtures" / "spring-petclinic" / "expected.json"


@pytest.mark.network
def test_petclinic_endpoints_match_hand_written_answer_key() -> None:
    from release_impact_brief.fetch import fetch_repo

    key = json.loads(FIXTURE.read_text())
    with fetch_repo(key["repo"], key["commit_sha"]) as fetched:
        result = extract_all(fetched.path, [JavaAdapter()])
    prefix = f"java:method:{key['package']}."
    found = {
        (n.attributes["http_method"], n.attributes["path"],
         n.attributes["handler"].removeprefix(prefix).split("(")[0])
        for n in result.nodes
        if n.kind == NodeKind.ENDPOINT
    }  # fmt: skip
    expected = {(e["method"], e["path"], e["handler"]) for e in key["endpoints"]}
    assert found == expected
