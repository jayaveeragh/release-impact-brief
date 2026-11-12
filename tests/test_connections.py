import json
from pathlib import Path
from typing import Any

import pytest

from release_impact_brief.adapters import Extraction
from release_impact_brief.facts import Confidence, EdgeKind, NodeKind
from release_impact_brief.pipeline import analyze

POM = "<project><groupId>shop</groupId><artifactId>{}</artifactId>{}</project>"


def dep(*artifacts: str) -> str:
    deps = "".join(f"<dependency><groupId>shop</groupId><artifactId>{a}</artifactId></dependency>"
                   for a in artifacts)  # fmt: skip
    return f"<dependencies>{deps}</dependencies>"


def app(package: str, name: str) -> str:
    return f"package {package};\n\n@SpringBootApplication\npublic class {name} {{}}\n"


FILES = {
    "pom.xml": POM.format(
        "shop",
        "<modules>"
        + "".join(
            f"<module>{m}</module>" for m in ("gateway", "orders", "billing", "web", "common")
        )
        + "</modules>",
    ),  # fmt: skip
    "common/pom.xml": POM.format("common", dep("spring-boot-starter-data-redis")),
    # --- gateway: routes in config
    "gateway/pom.xml": POM.format("gateway", ""),
    "gateway/src/main/java/g/GatewayApp.java": app("g", "GatewayApp"),
    "gateway/src/main/resources/application.yml": """\
spring:
  application:
    name: gateway
  cloud:
    gateway:
      routes:
        - id: orders
          uri: lb://orders
        - id: legacy
          uri: http://legacy.example.com
""",
    # --- orders: HTTP client, Feign, data stores, messaging
    "orders/pom.xml": POM.format("orders", dep("mysql-connector-j", "common", "spring-kafka")),
    "orders/src/main/java/o/OrdersApp.java": app("o", "OrdersApp"),
    "orders/src/main/resources/application-prod.yml": """\
spring:
  application:
    name: orders
  datasource:
    url: ${DB_URL:jdbc:postgresql://db:5432/orders}
  rabbitmq:
    host: rabbit
""",
    "orders/src/main/java/o/BillingClient.java": """\
package o;

import org.springframework.web.client.RestTemplate;

class BillingClient {
    private final String base = "http://billing/api";
    private final String docs = "not a url";
    String pay() { return "lb://billing/pay"; }
}
""",
    "orders/src/main/java/o/WebClientApi.java": """\
package o;

import org.springframework.cloud.openfeign.FeignClient;

@FeignClient(name = "web")
interface WebClientApi {}
""",
    "orders/src/main/java/o/Events.java": """\
package o;

class Events {
    private RabbitTemplate rabbit;
    void send() { rabbit.convertAndSend("orders.created", "x"); }

    @KafkaListener(topics = "payments")
    void onPayment(String event) {}
}
""",
    # --- billing: port 9000; a docs link that must not become a call
    "billing/pom.xml": POM.format("billing", ""),
    "billing/src/main/java/b/BillingApp.java": app("b", "BillingApp"),
    "billing/src/main/resources/application.yml": "server:\n  port: 9000\n",
    "billing/src/main/java/b/Docs.java": 'package b;\nclass D { String u = "http://orders/x"; }\n',
    # --- web: no port (Spring default 8080), calls a configured host
    "web/pom.xml": POM.format("web", ""),
    "web/src/main/java/w/WebApp.java": app("w", "WebApp"),
    "web/src/main/resources/application.yml": """\
remote:
  billing: http://localhost:9000
  unknown: http://localhost:7777
""",
    "web/src/main/java/w/Remote.java": """\
package w;

import org.springframework.web.client.RestClient;

class Remote {
    @Value("${remote.billing}") private String billing;
    @Value("${remote.unknown:http://x}") private String unknown;
    void lookup(DiscoveryClient d) { d.getInstances("orders"); }
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


def edges(facts: Extraction, kind: EdgeKind) -> dict[tuple[str, str], Confidence]:
    return {(e.src.removeprefix("service:"), e.dst.split(":")[-1]): e.confidence
            for e in facts.edges if e.kind == kind}  # fmt: skip


def service(facts: Extraction, name: str) -> dict[str, Any]:
    return next(n.attributes for n in facts.nodes if n.id == f"service:{name}")


def test_gateway_routes(facts: Extraction) -> None:
    assert edges(facts, EdgeKind.ROUTES) == {("gateway", "orders"): Confidence.EXACT}
    assert service(facts, "gateway")["unresolved_targets"] == [{
        "target": "http://legacy.example.com",
        "file": "gateway/src/main/resources/application.yml",
        "line": 10,
    }]  # fmt: skip


def test_http_requests(facts: Extraction) -> None:
    assert edges(facts, EdgeKind.REQUESTS) == {
        ("orders", "billing"): Confidence.EXACT,  # lb:// wins over http:// for the same pair
        ("orders", "web"): Confidence.EXACT,  # @FeignClient(name)
        ("web", "orders"): Confidence.LIKELY,  # getInstances("orders")
        ("web", "billing"): Confidence.GUESS,  # localhost:9000 = billing's port
    }
    # Docs.java has no HTTP client import, so its URL is not a call.
    assert "unresolved_targets" not in service(facts, "billing")
    unresolved = service(facts, "web")["unresolved_targets"]
    assert [u["target"] for u in unresolved] == ["http://localhost:7777"]


def test_data_stores(facts: Extraction) -> None:
    assert edges(facts, EdgeKind.USES) == {
        ("orders", "postgresql"): Confidence.EXACT,  # placeholder default in config
        ("orders", "mysql"): Confidence.LIKELY,  # driver on the classpath only
        ("orders", "redis"): Confidence.LIKELY,  # via the common module
        ("orders", "rabbitmq"): Confidence.EXACT,
        ("orders", "kafka"): Confidence.LIKELY,
    }
    stores = {n.name: n.attributes for n in facts.nodes if n.kind == NodeKind.DATA_STORE}
    assert stores["postgresql"]["locations"] == [{
        "value": "jdbc:postgresql://db:5432/orders",
        "file": "orders/src/main/resources/application-prod.yml",
        "line": 5,
    }]  # fmt: skip


def test_messaging(facts: Extraction) -> None:
    assert edges(facts, EdgeKind.PUBLISHES) == {("orders", "rabbitmq"): Confidence.EXACT}
    assert edges(facts, EdgeKind.CONSUMES) == {("orders", "kafka"): Confidence.EXACT}
    messaging = service(facts, "orders")["messaging"]
    assert [(m["direction"], m["broker"], m["destination"]) for m in messaging] == [
        ("consumes", "kafka", "payments"),
        ("publishes", "rabbitmq", None),
    ]


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.network
@pytest.mark.parametrize("repo", ["spring-petclinic", "spring-petclinic-microservices", "mall"])
def test_connections_match_hand_written_answer_key(repo: str) -> None:
    from release_impact_brief.fetch import fetch_repo

    key = json.loads((FIXTURES / repo / "expected.json").read_text())
    with fetch_repo(key["repo"], key["commit_sha"]) as fetched:
        facts = analyze(fetched.path, fetched.skipped_large)
    expected = key["connections"]
    for kind in ("routes", "requests", "uses"):
        found = {(s, d, c.value) for (s, d), c in edges(facts, EdgeKind(kind)).items()}
        assert found == {tuple(x) for x in expected[kind]}, kind
    found_messages = {
        (n.name, m["direction"], m["broker"], m["destination"])
        for n in facts.nodes
        if n.kind == NodeKind.SERVICE
        for m in n.attributes.get("messaging", [])
    }
    assert found_messages == {tuple(x) for x in expected["messaging"]}
