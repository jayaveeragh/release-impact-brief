import json
from pathlib import Path
from typing import Any

import pytest

from release_impact_brief.adapters import Extraction
from release_impact_brief.facts import EdgeKind, NodeKind
from release_impact_brief.system import analyze, classify

POM = "<project><groupId>com.shop</groupId><artifactId>{}</artifactId>{}</project>"


def app(package: str, name: str, *annotations: str) -> str:
    lines = "\n".join(f"@{a}" for a in ("SpringBootApplication", *annotations))
    return f"package {package};\n\n{lines}\npublic class {name} {{}}\n"


def write(root: Path, files: dict[str, str]) -> Extraction:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return analyze(root)


def services(facts: Extraction) -> dict[str, dict[str, Any]]:
    return {n.name: n.attributes for n in facts.nodes if n.kind == NodeKind.SERVICE}


MULTI = {
    "pom.xml": POM.format(
        "shop",
        "<modules><module>orders</module><module>billing</module>"
        "<module>gateway</module><module>common</module></modules>",
    ),
    "common/pom.xml": POM.format("common", ""),
    "orders/pom.xml": POM.format(
        "orders",
        "<dependencies><dependency><groupId>org.springframework"
        ".cloud</groupId><artifactId>spring-cloud-starter-netflix-eureka"
        "-client</artifactId></dependency></dependencies>",
    ),
    "orders/src/main/java/shop/orders/OrdersApp.java": app("shop.orders", "OrdersApp"),
    "orders/src/main/resources/application.yml": """\
spring:
  application:
    name: orders-service
  config:
    import: optional:configserver:http://config:8888
server:
  port: 8081
""",
    "orders/src/test/java/shop/orders/TestApp.java": app("shop.orders", "TestApp"),
    "billing/pom.xml": POM.format("billing", ""),
    "billing/src/main/java/shop/billing/BillingApp.java": app(
        "shop.billing", "BillingApp", "EnableDiscoveryClient"
    ),
    "gateway/pom.xml": POM.format("gateway", ""),
    "gateway/src/main/java/shop/gateway/GatewayApp.java": app("shop.gateway", "GatewayApp"),
    "gateway/src/main/resources/application.yml": """\
spring:
  cloud:
    gateway:
      routes:
        - id: orders
          uri: lb://orders-service
""",
    "docker-compose.yml": """\
services:
  orders-service:
    image: shop/orders-service:1.0
  billing:
    build: ./billing
  db:
    image: mysql:8
""",
}


@pytest.fixture(scope="module")
def multi(tmp_path_factory: pytest.TempPathFactory) -> Extraction:
    return write(tmp_path_factory.mktemp("multi"), MULTI)


def test_services_from_spring_boot_apps_in_main_code(multi: Extraction) -> None:
    found = services(multi)
    assert sorted(found) == ["billing", "gateway", "orders-service"]  # TestApp is test code
    orders = found["orders-service"]
    assert orders["name_source"] == "spring.application.name"
    assert orders["main_class"] == "java:class:shop.orders.OrdersApp"
    assert orders["module"] == "build:module:orders"
    assert orders["port"] == "8081"
    assert found["billing"]["name_source"] == "module"
    contains = {(e.src, e.dst) for e in multi.edges if e.kind == EdgeKind.CONTAINS}
    assert ("service:orders-service", "build:module:orders") in contains


def test_service_roles(multi: Extraction) -> None:
    found = services(multi)
    assert found["orders-service"]["roles"] == ["config_client", "discovery_client"]  # dep
    assert found["billing"]["roles"] == ["discovery_client"]  # annotation
    assert found["gateway"]["roles"] == ["gateway"]  # routes, not the dependency


def test_deployed_by_matches_compose_by_name_image_or_build_dir(multi: Extraction) -> None:
    found = services(multi)
    assert found["orders-service"]["deployed_by"] == [
        "deploy:compose:docker-compose.yml#orders-service"
    ]
    assert found["billing"]["deployed_by"] == ["deploy:compose:docker-compose.yml#billing"]
    assert found["gateway"]["deployed_by"] == []


def test_multi_module_microservices(multi: Extraction) -> None:
    result = classify(multi.nodes, multi.edges)
    assert result["layout"]["value"] == "multi-module"
    assert result["runtime"]["value"] == "microservices"
    texts = " | ".join(e["text"] for e in result["runtime"]["evidence"])
    assert "gateway routes" in texts and "discovery client" in texts
    assert all(e["file"] for e in result["runtime"]["evidence"])


def test_shared_database_makes_runtime_unclear(tmp_path: Path) -> None:
    db = "spring:\n  datasource:\n    url: jdbc:mysql://db:3306/shop?useSSL=false\n"
    facts = write(tmp_path, {
        "pom.xml": POM.format("shop", "<modules><module>a</module><module>b</module></modules>"),
        "a/pom.xml": POM.format("a", ""),
        "a/src/main/java/a/AApp.java": app("a", "AApp", "EnableDiscoveryClient"),
        "a/src/main/resources/application-prod.yml": db,
        "b/pom.xml": POM.format("b", ""),
        "b/src/main/java/b/BApp.java": app("b", "BApp"),
        "b/src/main/resources/application.yml": db.replace("?useSSL=false", ""),
    })  # fmt: skip
    runtime = classify(facts.nodes, facts.edges)["runtime"]
    assert runtime["value"] == "unclear"
    shared = [e for e in runtime["evidence"] if "share database" in e["text"]]
    assert len(shared) == 1 and "jdbc:mysql://db:3306/shop" in shared[0]["text"]


def test_several_apps_without_service_infrastructure_is_unclear(tmp_path: Path) -> None:
    facts = write(tmp_path, {
        "pom.xml": POM.format("shop", "<modules><module>a</module><module>b</module></modules>"),
        "a/pom.xml": POM.format("a", ""),
        "a/src/main/java/a/AApp.java": app("a", "AApp"),
        "b/pom.xml": POM.format("b", ""),
        "b/src/main/java/b/BApp.java": app("b", "BApp"),
    })  # fmt: skip
    assert classify(facts.nodes, facts.edges)["runtime"]["value"] == "unclear"


@pytest.mark.parametrize(
    ("files", "layout", "runtime"),
    [
        ({"pom.xml": POM.format("app", ""), "src/main/java/a/App.java": app("a", "App")},
         "single project", "monolith"),
        ({"pom.xml": POM.format("lib", "")}, "single project", "library"),
        ({"pom.xml": POM.format("p", "<modules><module>a</module></modules>"),
          "a/pom.xml": POM.format("a", ""), "a/src/main/java/a/App.java": app("a", "App")},
         "multi-module", "modular monolith"),
        ({"one/pom.xml": POM.format("one", ""), "two/build.gradle": ""}, "monorepo", "library"),
        ({"src/main/java/a/App.java": app("a", "App")}, "unclear", "monolith"),
    ],
)  # fmt: skip
def test_layout_and_runtime_rules(tmp_path: Path, files: dict[str, str], layout: str,
                                  runtime: str) -> None:  # fmt: skip
    facts = write(tmp_path, files)
    result = classify(facts.nodes, facts.edges)
    assert (result["layout"]["value"], result["runtime"]["value"]) == (layout, runtime)
    assert result["layout"]["evidence"] and result["runtime"]["evidence"]


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.network
@pytest.mark.parametrize("repo", ["spring-petclinic", "spring-petclinic-microservices", "mall"])
def test_structure_matches_hand_written_answer_key(repo: str) -> None:
    from release_impact_brief.fetch import fetch_repo

    key = json.loads((FIXTURES / repo / "expected.json").read_text())
    with fetch_repo(key["repo"], key["commit_sha"]) as fetched:
        facts = analyze(fetched.path, fetched.skipped_large)
    result = classify(facts.nodes, facts.edges)
    expected = key["structure"]
    assert result["layout"]["value"] == expected["layout"]
    assert result["runtime"]["value"] == expected["runtime"]
    found = {
        (n.name, n.attributes["module"].removeprefix("build:module:"),
         n.attributes["main_class"].rsplit(".", 1)[-1], tuple(n.attributes["roles"]))
        for n in facts.nodes
        if n.kind == NodeKind.SERVICE
    }  # fmt: skip
    assert found == {
        (s["name"], s["module"], s["main_class"], tuple(s["roles"])) for s in expected["services"]
    }
