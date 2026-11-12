"""How services connect: gateway routes, HTTP calls, data stores and messaging.

Runs after services exist. Every link is an edge with a confidence label:
- routes:   `lb://name` in gateway route config -> exact
- requests: `lb://name` or `@FeignClient(name)` -> exact; `http://name` or a bare
            service name (e.g. `getInstances("name")`) -> likely; `localhost:<port>`
            matched to the one service on that port -> guess
- uses:     data store configured (`spring.datasource.url`, `spring.data.redis.*`, ...)
            -> exact; only its driver/starter on the classpath -> likely
- publishes / consumes: messaging template calls and `@RabbitListener`-style handlers.
Targets that match no service are kept on the service as `unresolved_targets`
instead of being guessed. Only classes in files that import an HTTP client are
searched for URLs, so documentation links don't become calls.
"""

import re
from typing import Any

from release_impact_brief.adapters import Extraction
from release_impact_brief.facts import Confidence, Edge, EdgeKind, Node, NodeKind
from release_impact_brief.system import datasource_url, is_test, module_dir, properties

_RESOLVER = "connections"
_RANK = {Confidence.EXACT: 3, Confidence.LIKELY: 2, Confidence.GUESS: 1, Confidence.UNKNOWN: 0}
_URL = re.compile(r"^(lb|https?)://([\w.-]+)(?::(\d+))?")
_PLACEHOLDER = re.compile(r"^\$\{([^:}]+)(?::([^}]*))?\}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1"}
_ROUTE_URI = re.compile(r"^spring\.cloud\.gateway\..*routes\[\d+\]\.uri$")
_JDBC_KINDS = {"mysql", "mariadb", "postgresql", "h2", "hsqldb", "oracle", "sqlserver"}
# Config prefix -> store kind; keys ending in one of _LOCATION_KEYS give its location.
_CONFIG_STORES = (
    (re.compile(r"^spring\.data\.mongodb\."), "mongodb"),
    (re.compile(r"^spring\.(?:data\.)?redis\."), "redis"),
    (re.compile(r"^spring\.rabbitmq\."), "rabbitmq"),
    (re.compile(r"^spring\.kafka\."), "kafka"),
    (re.compile(r"^spring\.elasticsearch\."), "elasticsearch"),
)
_LOCATION_KEYS = (".host", ".uri", ".uris", ".addresses", ".bootstrap-servers")
_DEPENDENCY_STORES = {
    "mysql-connector-j": "mysql",
    "mysql-connector-java": "mysql",
    "mariadb-java-client": "mariadb",
    "postgresql": "postgresql",
    "h2": "h2",
    "hsqldb": "hsqldb",
    "spring-boot-starter-data-redis": "redis",
    "spring-boot-starter-data-mongodb": "mongodb",
    "spring-boot-starter-amqp": "rabbitmq",
    "spring-kafka": "kafka",
    "spring-boot-starter-data-elasticsearch": "elasticsearch",
}
_LISTENERS = {"RabbitListener": ("rabbitmq", "queues"), "KafkaListener": ("kafka", "topics"),
              "JmsListener": ("jms", "destination")}  # fmt: skip
_PUBLISH = re.compile(
    r"^(?:(RabbitTemplate|AmqpTemplate|RabbitMessagingTemplate)\.(?:convertAndSend|send)"
    r"|(KafkaTemplate)\.send|(JmsTemplate)\.(?:convertAndSend|send))\b"
)


def _literal(raw: str) -> str | None:
    return raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] == '"' else None


class _Links:
    def __init__(self, out: Extraction) -> None:
        self.out = out
        self.nodes = {n.id: n for n in out.nodes}
        self.services = [n for n in out.nodes if n.kind == NodeKind.SERVICE]
        self.by_name = {s.name: s for s in self.services}
        modules = {n.id.removeprefix("build:module:") for n in out.nodes
                   if n.kind == NodeKind.MODULE}  # fmt: skip
        self.module_dirs = modules
        self.service_of_module = {
            s.attributes["module"].removeprefix("build:module:"): s
            for s in self.services
            if s.attributes["module"]
        }
        self.configs: dict[str, list[Node]] = {}
        for n in out.nodes:
            if n.kind != NodeKind.CONFIG or n.attributes["config_type"] != "spring":
                continue
            if (owner := self.service_for(n.file)) is not None and not n.attributes["test"]:
                self.configs.setdefault(owner.id, []).append(n)
        self.ports: dict[str, set[str]] = {
            s.id: {str(p["value"]) for c in self.configs.get(s.id, [])
                   for k, p in properties(c) if k == "server.port"}
            for s in self.services
        }  # fmt: skip
        self.edges: dict[str, Edge] = {}
        self.stores: dict[str, dict[str, Any]] = {}

    def service_for(self, file: str) -> Node | None:
        directory = module_dir(file, self.module_dirs)
        if directory is None and len(self.services) == 1:
            return self.services[0]  # no build file: the one app owns all code
        return self.service_of_module.get(directory) if directory is not None else None

    def edge(self, kind: EdgeKind, src: Node, dst: str, conf: Confidence, file: str,
             line: int) -> None:  # fmt: skip
        edge_id = f"{kind}:{src.id}>{dst}"
        old = self.edges.get(edge_id)
        if old is None or _RANK[conf] > _RANK[old.confidence]:
            self.edges[edge_id] = Edge(edge_id, src.id, dst, kind, conf, file, line, _RESOLVER)

    def unresolved(self, service: Node, target: str, file: str, line: int) -> None:
        found = service.attributes.setdefault("unresolved_targets", [])
        entry = {"target": target, "file": file, "line": line}
        if entry not in found:
            found.append(entry)

    # ---- HTTP ------------------------------------------------------------

    def target(self, service: Node, text: str, file: str, line: int, *, kind: EdgeKind) -> None:
        """Link `service` to whatever `text` (a URL or a service name) points at."""
        match = _URL.match(text)
        if match is None:
            if text in self.by_name and text != service.name:
                self.edge(kind, service, self.by_name[text].id, Confidence.LIKELY, file, line)
            return
        scheme, host, port = match.groups()
        if host in self.by_name:
            if host != service.name:
                conf = Confidence.EXACT if scheme == "lb" else Confidence.LIKELY
                self.edge(kind, service, self.by_name[host].id, conf, file, line)
            return
        if host in _LOCAL_HOSTS and (on_port := self.on_port(port or "80")) is not None:
            if on_port.id != service.id:
                self.edge(kind, service, on_port.id, Confidence.GUESS, file, line)
            return
        self.unresolved(service, match.group(0), file, line)

    def on_port(self, port: str) -> Node | None:
        """The one service listening on `port` (8080 = a service that sets no port)."""
        found = [s for s in self.services if port in self.ports[s.id]]
        if not found and port == "8080":
            found = [s for s in self.services if not self.ports[s.id]]
        return found[0] if len(found) == 1 else None

    def config_value(self, service: Node, key: str) -> str | None:
        for config in self.configs.get(service.id, []):
            for k, prop in properties(config):
                if k == key:
                    return str(prop["value"])
        return None

    def http(self) -> None:
        for cls in [n for n in self.out.nodes if n.kind == NodeKind.CLASS]:
            if is_test(cls.file) or (service := self.service_for(cls.file)) is None:
                continue
            if "FeignClient" in cls.attributes["annotations"]:
                self.feign(service, cls)
            if "client_literals" not in cls.attributes:
                continue
            for lit in cls.attributes["client_literals"]:
                self.target(service, lit["value"], cls.file, lit["line"], kind=EdgeKind.REQUESTS)
            for field in self.fields(cls):
                for raw in (
                    field.attributes.get("annotation_args", {}).get("Value", {}).get("value", [])
                ):
                    found = _PLACEHOLDER.match(_literal(raw) or "")
                    if found is None:
                        continue
                    value = self.config_value(service, found.group(1)) or found.group(2)
                    if value:
                        self.target(service, value, field.file, field.start_line,
                                    kind=EdgeKind.REQUESTS)  # fmt: skip

    def feign(self, service: Node, cls: Node) -> None:
        args = cls.attributes.get("annotation_args", {}).get("FeignClient", {})
        names = [v for k in ("name", "value") for raw in args.get(k, []) if (v := _literal(raw))]
        urls = [v for raw in args.get("url", []) if (v := _literal(raw))]
        for name in names:
            if name in self.by_name and name != service.name:  # resolved by service discovery
                self.edge(EdgeKind.REQUESTS, service, self.by_name[name].id, Confidence.EXACT,
                          cls.file, cls.start_line)  # fmt: skip
            elif not urls and name not in self.by_name:
                self.unresolved(service, name, cls.file, cls.start_line)
        for url in urls:
            self.target(service, url, cls.file, cls.start_line, kind=EdgeKind.REQUESTS)

    def fields(self, cls: Node) -> list[Node]:
        prefix = f"java:field:{cls.attributes['qualified_name']}#"
        return [n for n in self.out.nodes if n.kind == NodeKind.FIELD and n.id.startswith(prefix)]

    def routes(self) -> None:
        for service in self.services:
            for config in self.configs.get(service.id, []):
                for key, prop in properties(config):
                    if _ROUTE_URI.match(key):
                        self.target(service, str(prop["value"]), config.file, prop["line"],
                                    kind=EdgeKind.ROUTES)  # fmt: skip

    # ---- data stores -----------------------------------------------------

    def store(self, kind: str, file: str, line: int, location: str | None) -> str:
        store_id = f"datastore:{kind}"
        entry = self.stores.setdefault(store_id, {"kind": kind, "file": file, "line": line,
                                                  "locations": []})  # fmt: skip
        if location:
            loc = {"value": location, "file": file, "line": line}
            if loc not in entry["locations"]:
                entry["locations"].append(loc)
        return store_id

    def module_dependencies(self, service: Node) -> list[tuple[str, Node]]:
        """(dependency, module declaring it) for the service's module and every repo
        module it depends on, transitively."""
        start = service.attributes["module"]
        if start is None:
            return []
        depends: dict[str, list[str]] = {}
        for e in self.out.edges:
            if e.kind == EdgeKind.DEPENDS_ON and e.src.startswith("build:module:"):
                depends.setdefault(e.src, []).append(e.dst)
        seen, pending, found = set(), [start], []
        while pending:
            module_id = pending.pop()
            if module_id in seen or module_id not in self.nodes:
                continue
            seen.add(module_id)
            module = self.nodes[module_id]
            found += [(d, module) for d in module.attributes.get("dependencies", [])]
            pending += depends.get(module_id, [])
        return found

    def data_stores(self) -> None:
        for service in self.services:
            for config in self.configs.get(service.id, []):
                for key, prop in properties(config):
                    value = str(prop["value"])
                    if key == "spring.datasource.url" and value:
                        url = datasource_url(value)
                        kind = url.split(":")[1] if url.startswith("jdbc:") else ""
                        if kind in _JDBC_KINDS:
                            dst = self.store(kind, config.file, prop["line"], url)
                            self.edge(EdgeKind.USES, service, dst, Confidence.EXACT, config.file,
                                      prop["line"])  # fmt: skip
                    for pattern, kind in _CONFIG_STORES:
                        if pattern.match(key):
                            location = value if key.endswith(_LOCATION_KEYS) else None
                            dst = self.store(kind, config.file, prop["line"], location)
                            self.edge(EdgeKind.USES, service, dst, Confidence.EXACT, config.file,
                                      prop["line"])  # fmt: skip
            for dependency, module in self.module_dependencies(service):
                from_dependency = _DEPENDENCY_STORES.get(dependency.rsplit(":", 1)[-1])
                if from_dependency is not None:
                    dst = self.store(from_dependency, module.file, 1, None)
                    self.edge(EdgeKind.USES, service, dst, Confidence.LIKELY, module.file, 1)

    # ---- messaging -------------------------------------------------------

    def messaging(self) -> None:
        for n in self.out.nodes:
            if n.kind not in (NodeKind.CLASS, NodeKind.METHOD) or is_test(n.file):
                continue
            if (service := self.service_for(n.file)) is None:
                continue
            found: list[tuple[str, str, str | None]] = []
            for annotation, (broker, key) in _LISTENERS.items():
                if annotation in n.attributes["annotations"]:
                    args = n.attributes.get("annotation_args", {}).get(annotation, {})
                    names: list[str | None] = [
                        v for raw in args.get(key, []) if (v := _literal(raw))
                    ]
                    found += [("consumes", broker, name) for name in names or [None]]
            for call in n.attributes.get("library_calls", []):
                if (m := _PUBLISH.match(call)) is not None:
                    broker = "rabbitmq" if m.group(1) else "kafka" if m.group(2) else "jms"
                    found.append(("publishes", broker, None))
            for direction, broker, destination in found:
                dst = self.store(broker, n.file, n.start_line, None)
                kind = EdgeKind.CONSUMES if direction == "consumes" else EdgeKind.PUBLISHES
                self.edge(kind, service, dst, Confidence.EXACT, n.file, n.start_line)
                entry = {"direction": direction, "broker": broker, "destination": destination,
                         "file": n.file, "line": n.start_line}  # fmt: skip
                messages = service.attributes.setdefault("messaging", [])
                if not any((m["direction"], m["broker"], m["destination"])
                           == (direction, broker, destination) for m in messages):  # fmt: skip
                    messages.append(entry)
        for service in self.services:
            if "messaging" in service.attributes:
                service.attributes["messaging"].sort(
                    key=lambda m: (m["direction"], m["broker"], m["destination"] or "")
                )

    def finish(self) -> None:
        for store_id, s in sorted(self.stores.items()):
            attributes = {"kind": s["kind"], "locations": s["locations"]}
            self.out.nodes.append(Node(store_id, NodeKind.DATA_STORE, s["kind"], s["file"],
                                       s["line"], s["line"], "config", attributes))  # fmt: skip
        self.out.edges += sorted(self.edges.values(), key=lambda e: e.id)


def add_connections(out: Extraction) -> None:
    links = _Links(out)
    links.routes()
    links.http()
    links.data_stores()
    links.messaging()
    links.finish()
