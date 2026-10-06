"use client";
import * as React from "react";
import dagre from "@dagrejs/dagre";
import {
  Background, BackgroundVariant, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, ReactFlowProvider, applyNodeChanges,
  type Edge, type Node, type NodeChange, type NodeProps, useReactFlow,
} from "@xyflow/react";
import { AlertTriangle, Lock } from "lucide-react";
import { NODE_META, NODE_TYPE_META, NodeStateIcon } from "@/components/status";
import { cn } from "@/lib/utils";
import { fmtDuration } from "@/lib/format";
import type { IREdge, IRNode, Issue, NodeState, RunNode } from "@/lib/types";

export const NODE_W = 232;
export const NODE_H = 68;

interface CardData extends Record<string, unknown> {
  node: IRNode;
  mode: "edit" | "run";
  rn?: RunNode;
  issues: Issue[];
  now: number;
}

const STATE_RING: Partial<Record<NodeState, string>> = {
  RUNNING: "border-ember shadow-[0_0_0_1px_rgb(var(--ember)/0.5),0_0_18px_rgb(var(--ember)/0.18)]",
  RETRYING: "border-warn", RECOVERING: "border-warn", WAITING_APPROVAL: "border-warn shadow-[0_0_0_1px_rgb(var(--warn)/0.4)]",
  SUCCESS: "border-ok/50", FAILED: "border-bad/70", VERIFICATION_FAILED: "border-bad/70", BLOCKED: "border-bad/50",
  READY: "border-info/50", SKIPPED: "border-line opacity-55", CANCELLED: "border-line opacity-55", PENDING: "border-line",
};

function ForgeNodeCard({ data, selected }: NodeProps<Node<CardData>>) {
  const { node, mode, rn, issues, now } = data;
  const M = NODE_TYPE_META[node.type];
  const errs = issues.filter((i) => i.severity === "error").length;
  const warns = issues.length - errs;
  const state = rn?.state;
  const elapsed = rn?.startedAt ? ((rn.finishedAt ? new Date(rn.finishedAt).getTime() : now) - new Date(rn.startedAt).getTime()) / 1000 : null;
  return (
    <div
      className={cn(
        "relative rounded-md border bg-panel px-3 py-2 transition-shadow",
        mode === "run" ? STATE_RING[state ?? "PENDING"] : errs ? "border-bad/70" : "border-line-strong",
        selected && "ring-2 ring-fg/70 ring-offset-1 ring-offset-bg",
      )}
      style={{ width: NODE_W, height: NODE_H }}
      data-state={state}
      data-testid={`node-${node.id}`}
    >
      <Handle type="target" position={Position.Left} isConnectable={mode === "edit"} />
      <div className="flex items-center gap-2">
        <M.icon className={cn("h-3.5 w-3.5 shrink-0", node.type === "AGENT" ? "text-ember" : "text-muted")} />
        <p className="min-w-0 flex-1 truncate text-sm font-medium leading-5">{node.name || node.id}</p>
        {mode === "run" && state && <NodeStateIcon state={state} />}
        {node.locked && <Lock className="h-3 w-3 text-faint" />}
        {mode === "edit" && (errs > 0 || warns > 0) && (
          <span className={cn("inline-flex items-center gap-0.5 text-2xs font-semibold", errs ? "text-bad" : "text-warn")}><AlertTriangle className="h-3 w-3" />{errs || warns}</span>
        )}
      </div>
      <div className="mt-1 flex items-center gap-1.5 text-2xs text-faint">
        <span className="mono truncate">{node.agentId ?? M.label}</span>
        {mode === "run" && rn ? (
          <>
            {rn.model && <span className="mono truncate rounded bg-raised px-1 text-muted" title={rn.routing?.reasons?.join("\n")}>{rn.model}</span>}
            <span className="ml-auto num shrink-0">{state && NODE_META[state].live ? fmtDuration(elapsed) : rn.attempt > 1 ? `try ${rn.attempt}` : elapsed !== null ? fmtDuration(elapsed) : ""}</span>
          </>
        ) : (
          <>
            {node.type === "AGENT" && <span className="ml-auto shrink-0">{node.tools?.length ?? 0} tools</span>}
          </>
        )}
      </div>
      <Handle type="source" position={Position.Right} isConnectable={mode === "edit"} />
    </div>
  );
}

const nodeTypes = { forge: ForgeNodeCard };

function layout(nodes: IRNode[], edges: IREdge[]): Record<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 26, ranksep: 78, marginx: 12, marginy: 12 });
  g.setDefaultEdgeLabel(() => ({}));
  nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));
  edges.forEach((e) => nodes.some((n) => n.id === e.source) && nodes.some((n) => n.id === e.target) && g.setEdge(e.source, e.target));
  // RECOVERY nodes have no edges: park them under the nodes they watch
  dagre.layout(g);
  const pos: Record<string, { x: number; y: number }> = {};
  nodes.forEach((n) => {
    const p = g.node(n.id);
    pos[n.id] = { x: (p?.x ?? 0) - NODE_W / 2, y: (p?.y ?? 0) - NODE_H / 2 };
  });
  const maxY = Math.max(0, ...Object.values(pos).map((p) => p.y));
  nodes.filter((n) => n.type === "RECOVERY").forEach((n, i) => {
    const w = (n.config?.watches ?? []).map((id: string) => pos[id]).filter(Boolean) as { x: number }[];
    pos[n.id] = { x: w.length ? w.reduce((a, b) => a + b.x, 0) / w.length : 0, y: maxY + NODE_H + 46 + i * 0 };
  });
  return pos;
}

export interface FlowProps {
  nodes: IRNode[];
  edges: IREdge[];
  mode: "edit" | "run";
  runNodes?: Record<string, RunNode>;
  issues?: Issue[];
  selectedNode?: string | null;
  selectedEdge?: string | null;
  now?: number;
  onSelectNode?: (id: string | null) => void;
  onSelectEdge?: (id: string | null) => void;
  onConnect?: (source: string, target: string) => void;
  onDeleteNodes?: (ids: string[]) => void;
  onDeleteEdges?: (ids: string[]) => void;
  className?: string;
}

function Inner(p: FlowProps) {
  const { nodes, edges, mode, runNodes, issues = [], now = 0 } = p;
  const rf = useReactFlow();
  const sig = nodes.map((n) => n.id).sort().join("|") + "#" + edges.map((e) => e.source + ">" + e.target).sort().join("|");
  const base = React.useMemo(() => layout(nodes, edges), [sig]); // eslint-disable-line react-hooks/exhaustive-deps
  const [moved, setMoved] = React.useState<Record<string, { x: number; y: number }>>({});
  React.useEffect(() => { setMoved({}); }, [sig]);
  React.useEffect(() => {
    const t = setTimeout(() => rf.fitView({ padding: 0.18, duration: 200, maxZoom: 1 }), 60);
    return () => clearTimeout(t);
  }, [sig, rf]);

  const rfNodes: Node<CardData>[] = nodes.map((n) => ({
    id: n.id, type: "forge", position: moved[n.id] ?? base[n.id] ?? { x: 0, y: 0 }, selected: p.selectedNode === n.id,
    data: { node: n, mode, rn: runNodes?.[n.id], issues: issues.filter((i) => i.node_id === n.id), now },
    draggable: true, deletable: mode === "edit" && !n.locked,
  }));

  const rfEdges: Edge[] = edges.filter((e) => nodes.some((n) => n.id === e.source) && nodes.some((n) => n.id === e.target)).map((e) => {
    let cls = "";
    if (mode === "run" && runNodes) {
      const s = runNodes[e.source]?.state, t = runNodes[e.target]?.state;
      if (s === "SKIPPED" || t === "SKIPPED") cls = "dead";
      else if (t === "RUNNING" || t === "RETRYING" || (s === "SUCCESS" && (t === "READY" || t === "WAITING_APPROVAL"))) cls = "live";
      else if (s === "SUCCESS" && t === "SUCCESS") cls = "done";
    }
    const label = e.condition ? e.condition : e.kind === "failure" ? "on failure" : e.kind === "control" ? "control" : undefined;
    return {
      id: e.id, source: e.source, target: e.target, type: "smoothstep", className: cls, selected: p.selectedEdge === e.id, label,
      labelStyle: { fill: "rgb(156,155,160)", fontSize: 10, fontFamily: "var(--font-mono)" }, labelBgStyle: { fill: "rgb(18,18,21)" },
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: "rgb(58,58,66)" },
      deletable: mode === "edit",
    };
  });

  const onNodesChange = React.useCallback((changes: NodeChange<Node<CardData>>[]) => {
    const next = applyNodeChanges(changes, rfNodes);
    setMoved((m) => {
      const o = { ...m };
      next.forEach((n) => { if (changes.some((c) => c.type === "position" && c.id === n.id)) o[n.id] = n.position; });
      return o;
    });
  }, [rfNodes]);

  return (
    <ReactFlow
      nodes={rfNodes}
      edges={rfEdges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onNodeClick={(_, n) => p.onSelectNode?.(n.id)}
      onEdgeClick={(_, e) => p.onSelectEdge?.(e.id)}
      onPaneClick={() => { p.onSelectNode?.(null); p.onSelectEdge?.(null); }}
      onConnect={(c) => c.source && c.target && p.onConnect?.(c.source, c.target)}
      onNodesDelete={(ns) => p.onDeleteNodes?.(ns.map((n) => n.id))}
      onEdgesDelete={(es) => p.onDeleteEdges?.(es.map((e) => e.id))}
      nodesConnectable={mode === "edit"}
      elementsSelectable
      deleteKeyCode={mode === "edit" ? ["Backspace", "Delete"] : null}
      minZoom={0.25}
      maxZoom={1.6}
      proOptions={{ hideAttribution: true }}
      fitView
    >
      <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="rgb(58,58,66)" />
      <Controls showInteractive={false} position="bottom-left" />
      <MiniMap pannable zoomable position="bottom-right" nodeColor={(n) => {
        const s = (n.data as CardData | undefined)?.rn?.state;
        return s === "SUCCESS" ? "rgb(62,207,142)" : s === "RUNNING" ? "rgb(255,106,43)" : s === "FAILED" ? "rgb(240,98,98)" : "rgb(58,58,66)";
      }} maskColor="rgba(0,0,0,0.5)" />
    </ReactFlow>
  );
}

export function FlowCanvas(props: FlowProps) {
  return (
    <div className={cn("h-full w-full", props.className)}>
      <ReactFlowProvider>
        <Inner {...props} />
      </ReactFlowProvider>
    </div>
  );
}
