// View spec -> React Flow graph. Groups become parent boxes, members sit inside them,
// links carry the fact's confidence as a CSS class, and anything outside the repo or
// inferred by the LLM is drawn dashed. ELK lays out the nested boxes.

import type { Edge, Node } from "@xyflow/react";
import type { ELK, ElkNode } from "elkjs/lib/elk-api";
import { factId, type ArchitectureSection, type FactInfo } from "./api";

export interface BoxData extends Record<string, unknown> {
  label: string;
  kind: string;
  dashed: boolean;
  inferred?: boolean;
  note?: string;
  factId?: string;
}

export type Box = Node<BoxData>;
export interface Graph { nodes: Box[]; edges: Edge[] }

const OUTSIDE = new Set(["external_system"]);
const BOX_HEIGHT = 44;
const boxWidth = (label: string) => Math.max(140, Math.min(320, label.length * 7.5 + 32));

export function buildGraph(section: ArchitectureSection, facts: Record<string, FactInfo>): Graph {
  const nodes: Box[] = [];
  const placed = new Set<string>();

  const factBox = (id: string, parentId?: string): Box => {
    const fact = facts[id];
    const label = fact?.name ?? id;
    return {
      id, parentId, type: "fact", position: { x: 0, y: 0 },
      width: boxWidth(label), height: BOX_HEIGHT,
      data: { label, kind: fact?.kind ?? "fact", dashed: OUTSIDE.has(fact?.kind ?? ""), factId: id },
    };
  };

  section.groups.forEach((group, i) => {
    const groupId = `group:${i}`;
    nodes.push({
      id: groupId, type: "group", position: { x: 0, y: 0 },
      data: { label: group.label, kind: "group", dashed: group.style === "dashed" },
    });
    for (const ref of group.members) {
      const id = factId(ref);
      if (!placed.has(id)) {
        placed.add(id);
        nodes.push(factBox(id, groupId));
      }
    }
  });
  for (const edge of section.edges) {
    for (const id of [factId(edge.from), factId(edge.to)]) {
      if (!placed.has(id)) {
        placed.add(id);
        nodes.push(factBox(id));
      }
    }
  }
  section.inferred.forEach((item, i) => {
    const label = item.label;
    nodes.push({
      id: `inferred:${i}`, type: "fact", position: { x: 0, y: 0 },
      width: boxWidth(label), height: BOX_HEIGHT,
      data: { label, kind: "inferred", dashed: true, inferred: true, note: item.reason },
    });
  });

  const edges: Edge[] = section.edges.map((edge, i) => {
    const tag = edge.inferred ? "inferred" : (edge.confidence ?? "unknown");
    return {
      id: `edge:${i}`, source: factId(edge.from), target: factId(edge.to),
      label: `${edge.label} (${tag})`, className: `conf-${tag}`,
      data: { ref: edge.ref ? factId(edge.ref) : null, reason: edge.reason ?? null },
    };
  });
  return { nodes, edges };
}

/** Positions from ELK (layered, left to right); child positions stay relative to their group. */
export async function layoutGraph(graph: Graph, elk: ELK): Promise<Graph> {
  const byParent = new Map<string | undefined, Box[]>();
  for (const n of graph.nodes) {
    byParent.set(n.parentId, [...(byParent.get(n.parentId) ?? []), n]);
  }
  const toElk = (n: Box): ElkNode => {
    const children = byParent.get(n.id) ?? [];
    return children.length > 0 || n.type === "group"
      ? { id: n.id, layoutOptions: { "elk.padding": "[top=36,left=16,bottom=16,right=16]" },
          children: children.map(toElk) }
      : { id: n.id, width: n.width, height: n.height };
  };
  const root: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.spacing.nodeNode": "28",
      "elk.layered.spacing.nodeNodeBetweenLayers": "64",
      "elk.edgeLabels.inline": "true",
    },
    children: (byParent.get(undefined) ?? []).map(toElk),
    edges: graph.edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  };
  const laid = await elk.layout(root);

  const placed = new Map<string, ElkNode>();
  const walk = (n: ElkNode) => {
    placed.set(n.id, n);
    n.children?.forEach(walk);
  };
  laid.children?.forEach(walk);
  const nodes = graph.nodes.map((n) => {
    const p = placed.get(n.id);
    const box: Box = { ...n, position: { x: p?.x ?? 0, y: p?.y ?? 0 } };
    if (n.type === "group") {
      box.width = p?.width;
      box.height = p?.height;
      box.style = { width: p?.width, height: p?.height };
    }
    return box;
  });
  return { nodes, edges: graph.edges };
}
