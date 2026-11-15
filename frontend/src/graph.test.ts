import ELK from "elkjs/lib/elk.bundled.js";
import { describe, expect, it } from "vitest";
import type { ArchitectureSection, FactInfo } from "./api";
import { buildGraph, layoutGraph } from "./graph";

const facts: Record<string, FactInfo> = {
  "service:web": { type: "node", kind: "service", name: "web", file: "web/App.java", line: 3 },
  "service:orders": { type: "node", kind: "service", name: "orders", file: "o/App.java", line: 3 },
  "datastore:mysql": { type: "node", kind: "data_store", name: "mysql", file: "pom.xml", line: 1 },
  "external:payments": { type: "node", kind: "external_system", name: "payments", file: "a.yml", line: 4 },
  "requests:service:web>service:orders": {
    type: "edge", kind: "requests", src: "service:web", dst: "service:orders",
    confidence: "likely", file: "web/C.java", line: 9,
  },
};

const section: ArchitectureSection = {
  type: "architecture_diagram",
  title: "How it fits",
  groups: [
    { label: "Apps", style: "solid", members: ["fact:service:web", "fact:service:orders"] },
    { label: "Storage", style: "solid", members: ["fact:datastore:mysql"] },
  ],
  edges: [
    { from: "fact:service:web", to: "fact:service:orders", label: "orders via",
      ref: "fact:requests:service:web>service:orders", inferred: false, confidence: "likely" },
    { from: "fact:service:orders", to: "fact:external:payments", label: "pays?",
      ref: null, inferred: true, reason: "callback URL in config", confidence: null },
  ],
  inferred: [{ label: "Mail provider", reason: "SMTP host in config", evidence: [] }],
};

describe("buildGraph", () => {
  const graph = buildGraph(section, facts);

  it("nests group members under their group, parents first", () => {
    const ids = graph.nodes.map((n) => n.id);
    expect(ids.indexOf("group:0")).toBeLessThan(ids.indexOf("service:web"));
    expect(graph.nodes.find((n) => n.id === "service:web")?.parentId).toBe("group:0");
    expect(graph.nodes.find((n) => n.id === "datastore:mysql")?.parentId).toBe("group:1");
  });

  it("adds boxes that only appear on links, dashed when outside the repo", () => {
    const payments = graph.nodes.find((n) => n.id === "external:payments");
    expect(payments?.parentId).toBeUndefined();
    expect(payments?.data.dashed).toBe(true);
    expect(payments?.data.label).toBe("payments");
  });

  it("shows inferred items as dashed notes", () => {
    const note = graph.nodes.find((n) => n.id === "inferred:0");
    expect(note?.data).toMatchObject({ label: "Mail provider", dashed: true, inferred: true,
      note: "SMTP host in config" });
  });

  it("styles links by the fact's confidence; inferred links are dashed", () => {
    const [real, guessed] = graph.edges;
    expect(real).toMatchObject({ source: "service:web", target: "service:orders",
      label: "orders via (likely)", className: "conf-likely" });
    expect(guessed).toMatchObject({ label: "pays? (inferred)", className: "conf-inferred" });
  });
});

describe("layoutGraph", () => {
  it("gives every box a position and groups a size that holds their members", async () => {
    const { nodes } = await layoutGraph(buildGraph(section, facts), new ELK());
    for (const n of nodes) {
      expect(Number.isFinite(n.position.x) && Number.isFinite(n.position.y)).toBe(true);
    }
    const group = nodes.find((n) => n.id === "group:0")!;
    const web = nodes.find((n) => n.id === "service:web")!;
    expect(web.position.x + (web.width ?? 0)).toBeLessThanOrEqual(group.width ?? 0);
    expect(web.position.y + (web.height ?? 0)).toBeLessThanOrEqual(group.height ?? 0);
  });
});
