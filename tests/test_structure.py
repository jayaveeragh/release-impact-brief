from typing import Any

import pytest

from release_impact_brief.adapters import Extraction, extract_all
from release_impact_brief.adapters.structure import StructureAdapter
from release_impact_brief.facts import Confidence, EdgeKind, NodeKind

POM = '<project xmlns="http://maven.apache.org/POM/4.0.0">{}</project>'

FILES = {
    "pom.xml": POM.format("""
  <parent><groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId></parent>
  <groupId>com.shop</groupId>
  <artifactId>shop</artifactId>
  <packaging>pom</packaging>
  <modules>
    <module>app</module>
    <module>lib</module>
    <module>missing</module>
  </modules>
  <dependencyManagement><dependencies><dependency>
    <groupId>com.shop</groupId><artifactId>lib</artifactId>
  </dependency></dependencies></dependencyManagement>
"""),
    "app/pom.xml": POM.format("""
  <parent><groupId>com.shop</groupId><artifactId>shop</artifactId></parent>
  <artifactId>app</artifactId>
  <dependencies>
    <dependency><groupId>${project.groupId}</groupId><artifactId>lib</artifactId></dependency>
    <dependency><groupId>com.mysql</groupId><artifactId>mysql-connector-j</artifactId>
      <scope>runtime</scope></dependency>
    <dependency><groupId>org.junit</groupId><artifactId>junit</artifactId>
      <scope>test</scope></dependency>
  </dependencies>
  <build><plugins><plugin>
    <groupId>org.springframework.boot</groupId><artifactId>spring-boot-maven-plugin</artifactId>
  </plugin></plugins></build>
"""),
    "lib/pom.xml": POM.format("""
  <parent><groupId>com.shop</groupId><artifactId>shop</artifactId></parent>
  <artifactId>lib</artifactId>
"""),
    "bomb/pom.xml": """<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>
<project><artifactId>&lol2;</artifactId></project>
""",
    "tools/settings.gradle": """\
rootProject.name = 'tools'
include 'core', ':cli'
""",
    "tools/core/build.gradle": """\
plugins { id 'java-library' }
dependencies {
    api 'com.google.guava:guava:33.0.0-jre'
    testImplementation 'org.junit.jupiter:junit-jupiter:5.10.0'
}
""",
    "tools/cli/build.gradle.kts": """\
plugins { id("org.springframework.boot") version "3.3.0" }
dependencies {
    implementation(project(":core"))
}
""",
    "app/Dockerfile": """\
FROM eclipse-temurin:21-jdk AS build
FROM eclipse-temurin:21-jre
EXPOSE 8080 9090
ENTRYPOINT ["java", \\
  "-jar", "app.jar"]
""",
    "docker-compose.yml": """\
services:
  app:
    build: ./app
    ports: ["8080:8080"]
    depends_on: [db]
    environment:
      DB_URL: jdbc:mysql://db/shop
      DB_PASSWORD: hunter2
  db:
    image: mysql:8
""",
    "k8s/app.yaml": """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
spec:
  template:
    spec:
      containers:
        - name: app
          image: shop/app:1.0
          ports:
            - containerPort: 8080
---
apiVersion: v1
kind: Secret
metadata:
  name: db
data:
  password: aHVudGVyMg==
""",
    "app/src/main/resources/application.yml": """\
spring:
  application:
    name: app
  datasource:
    url: jdbc:mysql://db/shop
    password: hunter2
server:
  port: 8080
---
spring:
  config:
    activate:
      on-profile: docker
server:
  port: 9000
""",
    "app/src/test/resources/application-test.properties": """\
# comment
spring.datasource.url=jdbc:h2:mem:test
app.api-key = ${API_KEY}
""",
    "chart/templates/deploy.yaml": "apiVersion: v1\nkind: Service\nmetadata:\n  name: {{ .N }}\n",
    ".github/workflows/ci.yml": "on: push\njobs: {}\n",
    ".env": "SECRET=do-not-read\n",
}


@pytest.fixture(scope="module")
def facts(tmp_path_factory: pytest.TempPathFactory) -> Extraction:
    root = tmp_path_factory.mktemp("repo")
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return extract_all(root, [StructureAdapter()])


def attrs(facts: Extraction) -> dict[str, dict[str, Any]]:
    return {n.id: n.attributes for n in facts.nodes}


def edges(facts: Extraction, kind: EdgeKind) -> dict[tuple[str, str], Confidence]:
    return {(e.src, e.dst): e.confidence for e in facts.edges if e.kind == kind}


def test_maven_modules(facts: Extraction) -> None:
    modules = {n.id: n for n in facts.nodes if n.kind == NodeKind.MODULE}
    app = modules["build:module:app"].attributes
    assert app["group_id"] == "com.shop"  # inherited from <parent>
    assert app["parent"] == "com.shop:shop"
    assert app["dependencies"] == ["com.shop:lib", "com.mysql:mysql-connector-j"]  # no test scope
    assert app["plugins"] == ["spring-boot-maven-plugin"]
    root = modules["build:module:."]
    assert (root.name, root.attributes["packaging"]) == ("shop", "pom")
    assert root.attributes["modules"] == ["app", "lib", "missing"]


def test_module_edges(facts: Extraction) -> None:
    assert edges(facts, EdgeKind.CONTAINS) == {
        ("build:module:.", "build:module:app"): Confidence.EXACT,
        ("build:module:.", "build:module:lib"): Confidence.EXACT,  # "missing" has no pom: no edge
        ("build:module:tools", "build:module:tools/cli"): Confidence.LIKELY,
        ("build:module:tools", "build:module:tools/core"): Confidence.LIKELY,
    }
    assert edges(facts, EdgeKind.EXTENDS) == {
        ("build:module:app", "build:module:."): Confidence.EXACT,
        ("build:module:lib", "build:module:."): Confidence.EXACT,
    }
    assert edges(facts, EdgeKind.DEPENDS_ON) == {
        ("build:module:app", "build:module:lib"): Confidence.EXACT,
        ("build:module:tools/cli", "build:module:tools/core"): Confidence.LIKELY,
        ("deploy:compose:docker-compose.yml#app", "deploy:compose:docker-compose.yml#db"): (
            Confidence.EXACT
        ),
    }


def test_gradle_is_read_as_text(facts: Extraction) -> None:
    a = attrs(facts)
    assert a["build:module:tools"]["name"] == "tools"
    assert a["build:module:tools/core"]["dependencies"] == ["com.google.guava:guava"]
    assert a["build:module:tools/cli"]["plugins"] == ["org.springframework.boot"]


def test_xml_entities_are_refused(facts: Extraction) -> None:
    assert facts.files_with_errors == ["bomb/pom.xml"]
    assert "build:module:bomb" not in attrs(facts)


def test_dockerfile(facts: Extraction) -> None:
    docker = attrs(facts)["deploy:dockerfile:app/Dockerfile"]
    assert docker["base_images"] == ["eclipse-temurin:21-jdk", "eclipse-temurin:21-jre"]
    assert docker["exposed_ports"] == ["8080", "9090"]
    assert docker["entrypoint"] == 'ENTRYPOINT ["java", "-jar", "app.jar"]'


def test_compose_services(facts: Extraction) -> None:
    nodes = {n.id: n for n in facts.nodes}
    app = nodes["deploy:compose:docker-compose.yml#app"]
    assert (app.name, app.start_line) == ("app", 2)
    assert app.attributes["build"] == "./app"
    assert app.attributes["depends_on"] == ["db"]
    assert app.attributes["environment"] == {
        "DB_URL": "jdbc:mysql://db/shop",
        "DB_PASSWORD": "<redacted>",
    }
    assert nodes["deploy:compose:docker-compose.yml#db"].attributes["image"] == "mysql:8"


def test_kubernetes_objects_without_secret_values(facts: Extraction) -> None:
    a = attrs(facts)
    deploy = a["deploy:k8s:k8s/app.yaml#Deployment/app"]
    assert deploy["images"] == ["shop/app:1.0"]
    assert deploy["ports"] == ["8080"]
    secret = a["deploy:k8s:k8s/app.yaml#Secret/db"]
    assert "aHVudGVyMg==" not in str(secret)
    # Helm templates aren't YAML until rendered: skipped, not reported as errors.
    assert not any("chart/" in node_id for node_id in a)


def test_spring_config_with_lines_profiles_and_redaction(facts: Extraction) -> None:
    a = attrs(facts)
    main = a["config:spring:app/src/main/resources/application.yml"]
    assert main["profile"] is None and main["test"] is False
    first, docker = main["documents"]
    assert first["properties"]["spring.application.name"] == {"value": "app", "line": 3}
    assert first["properties"]["spring.datasource.password"]["value"] == "<redacted>"
    assert docker["profile"] == "docker"
    assert docker["properties"]["server.port"] == {"value": "9000", "line": 15}

    test = a["config:spring:app/src/test/resources/application-test.properties"]
    assert test["profile"] == "test" and test["test"] is True
    props = test["documents"][0]["properties"]
    assert props["spring.datasource.url"] == {"value": "jdbc:h2:mem:test", "line": 2}
    assert props["app.api-key"]["value"] == "${API_KEY}"  # a placeholder is not a secret


def test_unrelated_files_are_not_read(facts: Extraction) -> None:
    files = {n.file for n in facts.nodes}
    assert ".env" not in files
    assert ".github/workflows/ci.yml" not in files
