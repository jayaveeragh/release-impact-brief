import json
from pathlib import Path
from typing import Any

import pytest

from release_impact_brief.adapters import Extraction
from release_impact_brief.impact import (
    CANDIDATES_SCHEMA,
    EXPLAIN_SCHEMA,
    analyze_impact,
    bug_text,
    explain,
    pick_candidates,
    search_facts,
    to_markdown,
)
from release_impact_brief.llm import FakeProvider, LlmRequest, LlmSession, Settings
from release_impact_brief.pipeline import analyze
from release_impact_brief.summaries import SummaryCache

FILES = {
    "pom.xml": "<project><groupId>s</groupId><artifactId>shop</artifactId></project>",
    "src/main/java/shop/ShopApp.java": "package shop;\n@SpringBootApplication\nclass ShopApp {}\n",
    "src/main/java/shop/Item.java": """\
package shop;

import jakarta.persistence.Entity;

@Entity
class Item {
    private int stock;
    public int getStock() { return stock; }
}
""",
    "src/main/java/shop/StockService.java": """\
package shop;

@Service
class StockService {
    int available(Item item) { return item.getStock(); }
    void onEvent() { }
}
""",
    "src/main/java/shop/ShopController.java": """\
package shop;

@Controller
class ShopController {
    private final StockService stock;

    @PostMapping("/buy")
    public String buy(Item item) {
        if (stock.available(item) <= 0) {
            return "sold-out";
        }
        return "ok";
    }

    @GetMapping("/stock")
    public String show(Item item) {
        if (item.getStock() > 10) {
            return "plenty";
        }
        return "few";
    }

    @GetMapping("/about")
    public String about() { return "about"; }
}
""",
    "src/test/java/shop/StockServiceTests.java": """\
package shop;

class StockServiceTests {
    private StockService stock;
    void counts() { stock.available(new Item()); }
}
""",
    "src/test/java/shop/ShopControllerTests.java": "package shop;\nclass ShopControllerTests {}\n",
}
VIEW: dict[str, Any] = {"repo": "https://github.com/acme/shop", "commit": "e" * 40,
                        "panels": {"modules": []}, "sections": []}  # fmt: skip


@pytest.fixture(scope="module")
def facts(tmp_path_factory: pytest.TempPathFactory) -> Extraction:
    root = tmp_path_factory.mktemp("shop")
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return analyze(root)


def labels(items: list[dict[str, Any]], kind: str) -> list[str]:
    return [i["label"] for i in items if i["item"] == kind]


def test_a_changed_service_method(facts: Extraction) -> None:
    brief = analyze_impact(facts, VIEW, ["java:method:shop.StockService#available(Item)"])
    assert labels(brief["high"], "endpoint") == ["POST /buy"]  # its handler calls it
    assert "GET /about" not in str(brief)
    assert labels(brief["high"], "branch") == []  # the change has no branches of its own
    assert labels(brief["low"], "branch") == ["shop.ShopController#buy(Item) line 9"]
    assert [t["label"] for t in brief["tests"]] == ["ShopControllerTests", "StockServiceTests"]
    assert brief["size"]["value"] == "looks small"
    assert brief["highlight"] == ["java:endpoint:POST /buy", "service:shop"]  # + its service


def test_a_changed_entity_field(facts: Extraction) -> None:
    brief = analyze_impact(facts, VIEW, ["java:field:shop.Item#stock"])
    high = brief["high"]
    assert set(labels(high, "endpoint")) == {"POST /buy", "GET /stock"}  # via getStock()
    assert "shop.ShopController#show(Item) line 17" in labels(high, "branch")  # reads it
    assert labels(high, "entity") == ["shop.Item"]
    assert brief["size"]["value"] == "looks big"
    assert "stored data" in brief["size"]["reasons"][0]


def _write(root: Path, extra: dict[str, str]) -> Extraction:
    for rel, text in {**FILES, **extra}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return analyze(root)


def test_conditions_that_read_a_changed_result_are_high(tmp_path: Path) -> None:
    facts = _write(
        tmp_path,
        {
            "src/main/java/shop/ShopController.java": """\
package shop;

@Controller
class ShopController {
    private final StockService stock;

    @PostMapping("/buy")
    public String buy(Item item) {
        int extra = item.getStock();
        if (extra > 100) {
            extra = 100;
        }
        int left = stock.available(item);
        int shown = left - 1;
        extra = shown;
        if (shown <= 0) {
            return "sold-out";
        }
        return "ok";
    }
}
"""
        },
    )
    brief = analyze_impact(facts, VIEW, ["java:method:shop.StockService#available(Item)"])
    branches = [i for i in brief["high"] + brief["low"] if i["item"] == "branch"]
    # Line 16 reads `shown`, from the changed call's result. Line 10 reads `extra` before
    # the call; the later `extra = shown` doesn't reach back to it.
    assert [b["line"] for b in branches] == [16]
    assert "returned" in branches[0]["reason"]


def test_fields_of_a_mapped_superclass_are_stored_data(tmp_path: Path) -> None:
    facts = _write(
        tmp_path,
        {
            "src/main/java/shop/Named.java": """\
package shop;

import jakarta.persistence.MappedSuperclass;

@MappedSuperclass
class Named {
    private String name;
}
"""
        },
    )
    brief = analyze_impact(facts, VIEW, ["java:field:shop.Named#name"])
    assert brief["size"]["value"] == "looks big"
    assert "stored data" in brief["size"]["reasons"][0]


def test_framework_called_code_is_reported_as_unseen(facts: Extraction) -> None:
    brief = analyze_impact(facts, VIEW, ["java:method:shop.StockService#onEvent()"])
    assert brief["size"]["value"] == "can't tell"
    assert any("onEvent() has no callers" in n for n in brief["couldnt_see"])


def test_nested_test_classes_count_as_their_outer_class(tmp_path: Path) -> None:
    files = dict(FILES)
    files["src/test/java/shop/StockServiceTests.java"] = """\
package shop;

class StockServiceTests {
    private StockService stock;
    @Nested
    class WhenEmpty {
        void counts() { stock.available(new Item()); }
    }
}
"""
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    brief = analyze_impact(analyze(tmp_path), VIEW,
                           ["java:method:shop.StockService#available(Item)"])  # fmt: skip
    assert "StockServiceTests" in [t["label"] for t in brief["tests"]]
    assert "WhenEmpty" not in str(brief["tests"])


def test_only_chosen_methods_are_reported_as_uncalled(facts: Extraction) -> None:
    brief = analyze_impact(facts, VIEW, ["java:entity:shop.Item"])  # pulls in getStock etc.
    assert not any("has no callers" in n for n in brief["couldnt_see"])


def test_every_item_keeps_its_evidence(facts: Extraction) -> None:
    brief = analyze_impact(facts, VIEW, ["java:method:shop.StockService#available(Item)"])
    endpoint = next(i for i in brief["high"] if i["item"] == "endpoint")
    assert endpoint["path"] == [
        "shop.ShopController#buy(Item)",
        "shop.StockService#available(Item)",
    ]
    assert endpoint["confidence"] in ("exact", "likely") and endpoint["file"].endswith(".java")
    refs = [i["ref"] for i in brief["high"] + brief["low"] + brief["tests"]]
    assert refs == [f"i{n}" for n in range(1, len(refs) + 1)]


def test_search_finds_locations_by_name(facts: Extraction) -> None:
    found = [f["id"] for f in search_facts(facts, "avail")]
    assert found == ["java:method:shop.StockService#available(Item)"]
    assert search_facts(facts, "a") == []  # too short to mean anything


def session(reply: Any) -> LlmSession:
    return LlmSession(FakeProvider(reply=reply), Settings("fake", "medium", False, 1.0, 1))


def test_candidates_come_only_from_the_shortlist(facts: Extraction, tmp_path: Path) -> None:
    def reply(request: LlmRequest) -> str:
        assert request.schema is CANDIDATES_SCHEMA
        return json.dumps({"picks": [{"ref": "c1", "reason": "matches stock"},
                                     {"ref": "c99", "reason": "made up"}]})  # fmt: skip

    text = bug_text("Sold-out items can still be bought", "stock check", "")
    result = pick_candidates(facts, VIEW, text, session(reply), SummaryCache(tmp_path / "c"))
    assert len(result["picked"]) == 1 and result["picked"][0]["reason"] == "matches stock"
    assert result["removed"] == [{"ref": "c99", "reason": "not in the shortlist"}]
    assert any("stock" in c["label"].lower() for c in result["shortlist"])


def test_explanation_keeps_only_cited_statements(facts: Extraction, tmp_path: Path) -> None:
    brief = analyze_impact(facts, VIEW, ["java:method:shop.StockService#available(Item)"])

    def reply(request: LlmRequest) -> str:
        assert request.schema is EXPLAIN_SCHEMA
        return json.dumps({"summary": [{"text": "Buying depends on it [i1].", "cites": ["i1"]},
                                       {"text": "It's 80% safe.", "cites": []}],
                           "what_to_test": [{"text": "Test buying.",
                                             "cites": ["i404"]}]})  # fmt: skip

    result = explain(brief, VIEW, "Bug: x", session(reply), SummaryCache(tmp_path / "c"))
    assert result["summary"] == [{"text": "Buying depends on it.", "cites": ["i1"]}]
    assert result["what_to_test"] == []
    assert result["removed"] == ["It's 80% safe.", "Test buying."]


def test_markdown_export(facts: Extraction) -> None:
    brief = analyze_impact(facts, VIEW, ["java:method:shop.StockService#available(Item)"])
    md = to_markdown(brief, VIEW, bug_text("Sold-out items can still be bought"))
    for heading in ("## Size: looks small", "## High impact", "## Low impact", "## What to test",
                    "## What the tool couldn't see"):  # fmt: skip
        assert heading in md
    assert "https://github.com/acme/shop/blob/" in md and "%" not in md


KEY = Path(__file__).parent / "fixtures" / "spring-petclinic" / "impact.json"
PKG = "org.springframework.samples.petclinic."


@pytest.mark.network
def test_seeded_petclinic_briefs_match_the_hand_written_expectations() -> None:
    from release_impact_brief.fetch import fetch_repo

    key = json.loads(KEY.read_text())
    with fetch_repo(key["repo"], key["commit_sha"]) as fetched:
        repo_facts = analyze(fetched.path)
    ids = [n.id for n in repo_facts.nodes]

    def resolve(short: str) -> str:
        kind, _, rest = short.partition(":") if short.startswith(("entity:", "endpoint:")) \
            else ("method", "", short)  # fmt: skip
        prefix = {"entity": "java:entity:" + PKG, "endpoint": "java:endpoint:",
                  "method": "java:method:" + PKG}[kind] + rest  # fmt: skip
        exact = kind != "method"  # endpoint/entity ids are complete; methods add a signature
        found = [i for i in ids if (i == prefix if exact else i.startswith(prefix))]
        assert found, short
        return found[0]

    for bug in key["bugs"]:
        brief = analyze_impact(repo_facts, {"sections": []}, [resolve(x) for x in bug["locations"]])
        expect = bug["expect"]
        high_ids = {i["id"] for i in brief["high"]}
        for want in expect["high_includes"]:
            assert resolve(want) in high_ids, (bug["id"], want)
        for not_want in expect["not_high"]:
            assert resolve(not_want) not in high_ids, (bug["id"], not_want)
        high_lines = {i["line"] for i in brief["high"] if i["item"] == "branch"}
        assert set(expect["branch_lines_high"]) <= high_lines, (bug["id"], high_lines)
        tests = {t["label"] for t in brief["tests"]}
        assert set(expect["tests_include"]) <= tests, (bug["id"], tests)
        assert brief["size"]["value"] == expect["size"], (bug["id"], brief["size"])
        for word in expect.get("couldnt_see_mentions", []):
            assert any(word in n for n in brief["couldnt_see"]), (bug["id"], word)
