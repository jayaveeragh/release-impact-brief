import {
  Background, Controls, Handle, MiniMap, Position, ReactFlow, type NodeProps,
} from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";
import { useEffect, useMemo, useState } from "react";
import type { ArchitectureSection, FactInfo } from "./api";
import { buildGraph, layoutGraph, type Box, type Graph } from "./graph";

const elk = new ELK();

function FactBox({ data }: NodeProps<Box>) {
  return (
    <div className={`box kind-${data.kind}${data.dashed ? " dashed" : ""}`} title={data.note}>
      <Handle type="target" position={Position.Left} />
      <span className="box-kind">{data.inferred ? "inferred" : data.kind.replace("_", " ")}</span>
      <span className="box-label">{data.label}</span>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function GroupBox({ data }: NodeProps<Box>) {
  return <div className={`group-box${data.dashed ? " dashed" : ""}`}><span>{data.label}</span></div>;
}

const nodeTypes = { fact: FactBox, group: GroupBox };

export function Diagram({ section, facts, onSelect }: {
  section: ArchitectureSection;
  facts: Record<string, FactInfo>;
  onSelect: (factId: string) => void;
}) {
  const graph = useMemo(() => buildGraph(section, facts), [section, facts]);
  const [laid, setLaid] = useState<Graph | null>(null);
  useEffect(() => {
    let live = true;
    layoutGraph(graph, elk).then((g) => live && setLaid(g));
    return () => { live = false; };
  }, [graph]);

  if (!laid) return <div className="diagram loading">Laying out…</div>;
  return (
    <div className="diagram">
      <ReactFlow
        nodes={laid.nodes}
        edges={laid.edges}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.1}
        nodesConnectable={false}
        onNodeClick={(_, node) => node.data.factId && onSelect(node.data.factId)}
        onEdgeClick={(_, edge) => {
          const ref = (edge.data as { ref: string | null } | undefined)?.ref;
          if (ref) onSelect(ref);
        }}
      >
        <Background />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable position="top-right" />
      </ReactFlow>
      <div className="legend">
        <span className="conf-exact">exact</span><span className="conf-likely">likely</span>
        <span className="conf-guess">guess</span><span className="conf-inferred">inferred / unknown</span>
        <span className="dashed-sample">outside this repo</span>
      </div>
    </div>
  );
}
