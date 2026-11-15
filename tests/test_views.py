import json
import re
from pathlib import Path
from typing import Any

import pytest

from release_impact_brief.adapters import Extraction
from release_impact_brief.facts import Confidence
from release_impact_brief.llm import FakeProvider, LlmRequest, LlmSession, Settings
from release_impact_brief.pipeline import analyze
from release_impact_brief.summaries import Summarizer, SummaryCache
from release_impact_brief.views import VIEW_SCHEMA, FactTable, generate_view, validate_view

POM = "<project><groupId>shop</groupId><artifactId>{}</artifactId>{}</project>"
FILES = {
    "pom.xml": POM.format("shop", "<modules><module>web</module><module>orders</module></modules>"),
    "web/pom.xml": POM.format("web", ""),
    "web/src/main/java/w/WebApp.java": "package w;\n@SpringBootApplication\nclass WebApp {}\n",
    "web/src/main/java/w/Front.java": """\
package w;

import org.springframework.web.client.RestTemplate;

@RestController
class Front {
    String base = "http://orders/api";

    @GetMapping("/buy")
    public String buy() { return ""; }
}
""",
    "web/src/main/resources/application.yml": "spring:\n  application:\n    name: web\n",
    "orders/pom.xml": POM.format(
        "orders",
        "<dependencies><dependency><groupId>x</groupId>"
        "<artifactId>postgresql</artifactId></dependency></dependencies>",
    ),  # fmt: skip
    "orders/src/main/java/o/OrdersApp.java": (
        "package o;\n@SpringBootApplication\npublic class OrdersApp {}\n"
    ),
    "orders/src/main/resources/application.yml": "spring:\n  application:\n    name: orders\n",
}


@pytest.fixture(scope="module")
def facts(tmp_path_factory: pytest.TempPathFactory) -> Extraction:
    root = tmp_path_factory.mktemp("shop")
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return analyze(root)


@pytest.fixture(scope="module")
def table(facts: Extraction) -> FactTable:
    return FactTable(facts)


def alias(table: FactTable, real_id: str) -> str:
    return next(a for a, r in table.aliases.items() if r == real_id)


# ---- the schema the API is asked to follow ---------------------------------------


def test_schema_follows_structured_output_rules() -> None:
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(VIEW_SCHEMA)
    assert "$ref" not in json.dumps(VIEW_SCHEMA)  # flat: no recursion to worry about


# ---- fact table ------------------------------------------------------------------


def test_fact_table_lists_system_level_facts_with_short_ids(table: FactTable) -> None:
    text = table.render()
    web, orders = alias(table, "service:web"), alias(table, "service:orders")
    assert re.search(rf"^{web} service web\b", text, re.M)
    requests = alias(table, "requests:service:web>service:orders")
    assert f"{requests} {web} requests {orders} (likely)" in text
    assert alias(table, "java:endpoint:GET /buy").startswith("f")
    assert requests.startswith("e")
    assert "java:class" not in table.aliases.values()  # classes are below this level


# ---- validator -------------------------------------------------------------------


def architecture(table: FactTable, **overrides: Any) -> dict[str, Any]:
    web, orders = alias(table, "service:web"), alias(table, "service:orders")
    section = {
        "type": "architecture_diagram",
        "title": "How it fits",
        "groups": [
            {"label": "Apps", "style": "solid", "members": [f"fact:{web}", f"fact:{orders}"]}
        ],  # fmt: skip
        "edges": [
            {
                "from": f"fact:{web}",
                "to": f"fact:{orders}",
                "label": "buys via",
                "ref": f"fact:{alias(table, 'requests:service:web>service:orders')}",
                "inferred_reason": None,
            }
        ],  # fmt: skip
        "inferred": [],
    }
    section.update(overrides)
    return section


def test_valid_references_resolve_to_real_fact_ids(table: FactTable) -> None:
    [section], removed = validate_view({"sections": [architecture(table)]}, table)
    assert removed == []
    assert section["groups"][0]["members"] == ["fact:service:web", "fact:service:orders"]
    edge = section["edges"][0]
    assert edge["ref"] == "fact:requests:service:web>service:orders"
    assert edge["confidence"] == Confidence.LIKELY  # taken from the fact, never from the LLM
    assert edge["inferred"] is False


def test_invented_nodes_never_survive(table: FactTable) -> None:
    web = alias(table, "service:web")
    raw = architecture(table, groups=[
        {"label": "Apps", "style": "solid",
         "members": [f"fact:{web}", "fact:f999", "fact:service:payments", "payments"]},
        {"label": "Ghosts", "style": "dashed", "members": ["fact:f998"]},
    ])  # fmt: skip
    [section], removed = validate_view({"sections": [raw]}, table)
    assert [g["label"] for g in section["groups"]] == ["Apps"]  # empty group dropped
    assert section["groups"][0]["members"] == ["fact:service:web"]
    reasons = {(r["ref"], r["reason"]) for r in removed}
    assert ("fact:f999", "no such fact") in reasons
    assert ("fact:service:payments", "no such fact") in reasons
    assert ("payments", "not a fact reference") in reasons
    assert any(r["where"].endswith("groups[1]") and "empty" in r["reason"] for r in removed)


def test_a_bare_short_id_from_the_table_is_accepted(table: FactTable) -> None:
    web, orders = alias(table, "service:web"), alias(table, "service:orders")
    requests = alias(table, "requests:service:web>service:orders")
    raw = architecture(table, edges=[{"from": web, "to": orders, "label": "buys via",
                                      "ref": requests, "inferred_reason": None}])  # fmt: skip
    [section], removed = validate_view({"sections": [raw]}, table)
    assert removed == []
    assert section["edges"][0]["ref"] == "fact:requests:service:web>service:orders"


def test_edges_need_a_matching_fact_or_a_stated_reason(table: FactTable) -> None:
    web, orders = alias(table, "service:web"), alias(table, "service:orders")
    pg = alias(table, "datastore:postgresql")
    uses = alias(table, "uses:service:orders>datastore:postgresql")
    edges = [
        # invented link
        {"from": f"fact:{web}", "to": f"fact:{pg}", "label": "x", "ref": "fact:e999",
         "inferred_reason": None},
        # real link, wrong boxes: orders uses postgres, web doesn't
        {"from": f"fact:{web}", "to": f"fact:{pg}", "label": "x", "ref": f"fact:{uses}",
         "inferred_reason": None},
        # no fact, no reason
        {"from": f"fact:{web}", "to": f"fact:{pg}", "label": "x", "ref": None,
         "inferred_reason": None},
        # no fact, but a reason: kept, marked inferred
        {"from": f"fact:{orders}", "to": f"fact:{web}", "label": "notifies?", "ref": None,
         "inferred_reason": "a callback URL in config"},
        # a node used as an edge
        {"from": f"fact:{orders}", "to": f"fact:{pg}", "label": "x", "ref": f"fact:{web}",
         "inferred_reason": None},
    ]  # fmt: skip
    [section], removed = validate_view({"sections": [architecture(table, edges=edges)]}, table)
    assert [(e["label"], e["inferred"], e["confidence"]) for e in section["edges"]] == [
        ("notifies?", True, None)]  # fmt: skip
    reasons = [r["reason"] for r in removed]
    assert "no such fact" in reasons
    assert any("connects service:orders to datastore:postgresql" in r for r in reasons)
    assert "no fact and no reason given" in reasons
    assert "is a node, not an edge" in reasons


def test_flows_and_narratives(table: FactTable) -> None:
    web, buy = alias(table, "service:web"), alias(table, "java:endpoint:GET /buy")
    sections = [
        {"type": "flow", "title": "Buying", "description": "d",
         "steps": [f"fact:{buy}", "fact:f999", f"fact:{web}"]},
        {"type": "flow", "title": "Ghost flow", "description": "d",
         "steps": ["fact:f997", f"fact:{web}"]},
        {"type": "narrative", "title": "Overview",
         "body": f"The web app [fact:{web}] calls a payment API [fact:f996]."},
    ]  # fmt: skip
    kept, removed = validate_view({"sections": sections}, table)
    assert [s["title"] for s in kept] == ["Buying", "Overview"]
    assert kept[0]["steps"] == ["fact:java:endpoint:GET /buy", "fact:service:web"]
    assert kept[1]["body"] == "The web app [fact:service:web] calls a payment API."
    assert any(r["where"].startswith("sections[1]") and "fewer than 2" in r["reason"]
               for r in removed)  # fmt: skip


def test_grouped_citations_are_checked_one_by_one(table: FactTable) -> None:
    web, orders = alias(table, "service:web"), alias(table, "service:orders")
    raw = {"type": "narrative", "title": "T",
           "body": f"Two apps [fact:{web}, fact:{orders}, fact:f999] talk."}  # fmt: skip
    [kept], removed = validate_view({"sections": [raw]}, table)
    assert kept["body"] == "Two apps [fact:service:web][fact:service:orders] talk."
    assert [r["ref"] for r in removed] == ["fact:f999"]


def test_inferred_items_need_a_reason(table: FactTable) -> None:
    inferred = [
        {"label": "Mail provider", "reason": "SMTP host in config",
         "evidence": [f"fact:{alias(table, 'service:web')}", "fact:f999"]},
        {"label": "Something", "reason": "", "evidence": []},
    ]  # fmt: skip
    [section], removed = validate_view({"sections": [architecture(table, inferred=inferred)]},
                                       table)  # fmt: skip
    assert section["inferred"] == [{"label": "Mail provider", "reason": "SMTP host in config",
                                    "evidence": ["fact:service:web"]}]  # fmt: skip
    assert any(r["reason"] == "inferred item without a reason" for r in removed)


# ---- generation, end to end --------------------------------------------------------


def test_generated_view_holds_only_real_facts(facts: Extraction, table: FactTable,
                                              tmp_path: Path) -> None:  # fmt: skip
    """The Phase 3 done check: invented nodes never survive validation."""
    web = alias(table, "service:web")

    def reply(request: LlmRequest) -> str:
        if request.schema is VIEW_SCHEMA:
            raw = {"sections": [architecture(table, groups=[
                {"label": "Apps", "style": "solid",
                 "members": [f"fact:{web}", "fact:f999", "fact:service:invented"]}])]}  # fmt: skip
            return json.dumps(raw)
        return FakeProvider().complete(request).text

    cache = SummaryCache(tmp_path / "c.sqlite")
    session = LlmSession(FakeProvider(reply=reply), Settings("fake", "medium", False, 1.0, 1))
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    repo_facts = analyze(root)
    summaries = Summarizer(repo_facts, root, "https://github.com/x/shop", "b" * 40, session,
                           cache).run()  # fmt: skip
    view = generate_view(repo_facts, summaries, "https://github.com/x/shop", "b" * 40, session,
                         cache)  # fmt: skip
    real = {n.id for n in repo_facts.nodes} | {e.id for e in repo_facts.edges}
    refs = re.findall(r'"fact:([^"\]]+)', json.dumps(view["sections"]))
    assert refs and all(r in real for r in refs)
    assert set(view["facts"]) == set(refs)  # every referenced fact is described for the renderer
    assert view["runtime_style"]["value"] in ("microservices", "unclear")  # from the classifier
    assert {r["ref"] for r in view["removed"]} >= {"fact:f999", "fact:service:invented"}
