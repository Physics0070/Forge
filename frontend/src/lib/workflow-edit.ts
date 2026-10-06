import type { Agent, IR, IREdge, IRNode, NodeType, ToolDef } from "./types";

export function uniqueId(base: string, taken: Iterable<string>): string {
  const set = new Set(taken);
  const slug = base.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") || "node";
  const id = /^[a-z]/.test(slug) ? slug : `n_${slug}`;
  if (!set.has(id)) return id;
  let i = 2;
  while (set.has(`${id}_${i}`)) i++;
  return `${id}_${i}`;
}

const TYPE_DEFAULTS: Record<Exclude<NodeType, "AGENT">, Partial<IRNode>> = {
  PARALLEL: { name: "Fan out" },
  JOIN: { name: "Join", config: { required: [], mode: "all", concat: {} } },
  CONDITION: { name: "Condition", config: { predicate: { path: "$.value", op: "gt", value: 0 } } },
  RETRY: { name: "Retry gate", config: { target: "", until: { path: "$.ok", op: "eq", value: true }, max_iterations: 2 } },
  APPROVAL: { name: "Human approval", config: { kind: "approve_action", title: "Approve", on_reject: "fail", summary: "" } },
  VERIFICATION: { name: "Verify findings", outputSchema: { type: "object" } },
  RECOVERY: { name: "Recovery", config: { watches: [], fallback_output: {} } },
  TRANSFORM: { name: "Transform", config: { select: {}, set: {} } },
};

export function newNode(type: NodeType, taken: string[], agent?: Agent, name?: string): IRNode {
  if (type === "AGENT") {
    const a = agent!;
    return {
      id: uniqueId(a.id, taken), type, name: name || a.name, agentId: a.id, inputSchema: a.inputSchema, outputSchema: a.outputSchema,
      tools: [...a.tools], permissions: [...a.permissions], routing: { ...a.routing }, timeoutS: a.timeoutS,
      retryPolicy: { maxAttempts: 2, backoff: "exponential", baseDelayS: 1, maxDelayS: 30, jitter: 0.25 },
    };
  }
  const d = TYPE_DEFAULTS[type];
  return { id: uniqueId(type, taken), type, name: name || d.name, config: structuredClone(d.config ?? {}), outputSchema: d.outputSchema, inputSchema: undefined };
}

function props(schema?: Record<string, any>): Record<string, any> {
  return (schema?.properties as Record<string, any>) ?? {};
}

/** Same-name field mapping source.output -> target.input; whole-output when the target takes the source's exact schema. */
export function autoMapping(src: IRNode, dst: IRNode): { from: string; to: string }[] {
  const sp = props(src.outputSchema);
  const out: { from: string; to: string }[] = [];
  for (const [k, v] of Object.entries(props(dst.inputSchema))) {
    if (k in sp) out.push({ from: `$.${k}`, to: k });
    else if (src.outputSchema && JSON.stringify(v) === JSON.stringify(src.outputSchema)) out.push({ from: "$", to: k });
  }
  return out;
}

export function addEdge(ir: IR, source: string, target: string): IR {
  if (source === target) return ir;
  const id = uniqueId(`${source}__${target}`, ir.edges.map((e) => e.id));
  if (ir.edges.some((e) => e.source === source && e.target === target)) return ir;
  const s = ir.nodes.find((n) => n.id === source)!;
  const t = ir.nodes.find((n) => n.id === target)!;
  const edge: IREdge = { id, source, target, kind: "data" };
  if (s.type === "CONDITION") edge.condition = ir.edges.some((e) => e.source === source && e.condition === "true") ? "false" : "true";
  const mapping = s.type === "AGENT" && t.type === "AGENT" ? autoMapping(s, t) : [];
  if (mapping.length) edge.mapping = mapping;
  return { ...ir, edges: [...ir.edges, edge] };
}

export function removeNodes(ir: IR, ids: string[]): IR {
  const gone = new Set(ids);
  return {
    ...ir,
    nodes: ir.nodes
      .filter((n) => !gone.has(n.id))
      .map((n) => {
        const cfg = { ...(n.config ?? {}) };
        if (Array.isArray(cfg.required)) cfg.required = cfg.required.filter((x: string) => !gone.has(x));
        if (Array.isArray(cfg.watches)) cfg.watches = cfg.watches.filter((x: string) => !gone.has(x));
        if (gone.has(cfg.target)) cfg.target = "";
        return { ...n, config: cfg };
      }),
    edges: ir.edges.filter((e) => !gone.has(e.source) && !gone.has(e.target)),
    outputs: (ir.outputs ?? []).filter((o) => !gone.has(o)),
  };
}

export function updateNode(ir: IR, id: string, patch: Partial<IRNode>): IR {
  return { ...ir, nodes: ir.nodes.map((n) => (n.id === id ? { ...n, ...patch } : n)) };
}
export function updateEdge(ir: IR, id: string, patch: Partial<IREdge>): IR {
  return { ...ir, edges: ir.edges.map((e) => (e.id === id ? { ...e, ...patch } : e)) };
}

/** Permissions a node needs to use its tools (mirrors the server-side check, for instant feedback). */
export function requiredPermissions(tools: string[], defs: ToolDef[]): string[] {
  return [...new Set(tools.flatMap((t) => defs.find((d) => d.id === t)?.permissions ?? []))];
}

export function sameJson(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** IR as the API stores it: drop server-owned identity/time fields before sending a new version. */
export function toSubmittable(ir: IR): IR {
  const { id: _id, projectId: _p, version: _v, ...rest } = ir as IR & { createdBy?: string; createdAt?: string; updatedAt?: string };
  void _id; void _p; void _v;
  const r = rest as IR & { createdBy?: string; createdAt?: string; updatedAt?: string };
  delete r.createdBy; delete r.createdAt; delete r.updatedAt;
  return r;
}
