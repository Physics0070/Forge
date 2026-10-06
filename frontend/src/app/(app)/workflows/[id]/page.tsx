"use client";
import * as React from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { AlertTriangle, CheckCircle2, Copy, Download, GitBranchPlus, Pencil, Play, Plus, Save, ShieldCheck, Trash2, Undo2 } from "lucide-react";
import { FlowCanvas } from "@/components/flow";
import { EdgeInspector, NodeInspector } from "@/components/node-inspector";
import { ExecuteDialog } from "@/components/execute-dialog";
import { NODE_TYPE_META } from "@/components/status";
import { CodeBlock } from "@/components/viz";
import { Badge, Button, ErrorState, Field, Input, Kv, Panel, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Dialog, Tabs, Tip, useToast } from "@/components/ui/overlay";
import { ApiError, api } from "@/lib/api";
import { useDebounced, useQuery } from "@/lib/hooks";
import { addEdge, newNode, removeNodes, sameJson, toSubmittable, updateEdge, updateNode } from "@/lib/workflow-edit";
import { fmtDateTime, shortId } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Agent, IR, Issue, NodeType, ToolDef, Workflow, WorkflowVersion } from "@/lib/types";

const TYPES: NodeType[] = ["AGENT", "PARALLEL", "JOIN", "CONDITION", "RETRY", "APPROVAL", "VERIFICATION", "RECOVERY", "TRANSFORM"];

function WorkflowPageInner() {
  const { id } = useParams<{ id: string }>();
  const sp = useSearchParams();
  const router = useRouter();
  const { push } = useToast();
  const wf = useQuery<Workflow>(`/api/workflows/${id}`);
  const catalog = useQuery<{ agents: Agent[]; tools: ToolDef[] }>("/api/agents");
  const settings = useQuery<{ models: { routerTiers: Record<string, string>; default: string | null } }>("/api/settings");

  const vParam = sp.get("v");
  const [ir, setIr] = React.useState<IR | null>(null);
  const [saved, setSaved] = React.useState<IR | null>(null);
  const [version, setVersion] = React.useState<WorkflowVersion | null>(null);
  const [editing, setEditing] = React.useState(false);
  const [sel, setSel] = React.useState<{ kind: "node" | "edge"; id: string } | null>(null);
  const [panel, setPanel] = React.useState<"inspect" | "validation" | "versions" | "json">("inspect");
  const [issues, setIssues] = React.useState<Issue[]>([]);
  const [addOpen, setAddOpen] = React.useState(false);
  const [execOpen, setExecOpen] = React.useState(false);
  const [delOpen, setDelOpen] = React.useState(false);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [saveNote, setSaveNote] = React.useState("");
  const [saveOpen, setSaveOpen] = React.useState(false);

  // Load the selected (or latest) version into the working copy.
  React.useEffect(() => {
    const w = wf.data;
    if (!w) return;
    const target = vParam ? w.versions.find((v) => v.version === Number(vParam)) : w.latestVersion;
    if (!target) return;
    (async () => {
      const full = target.ir ? target : await api.get<WorkflowVersion>(`/api/workflows/${id}/versions/${target.version}`);
      setVersion(full);
      setIr(structuredClone(full.ir!));
      setSaved(structuredClone(full.ir!));
      setIssues(full.issues ?? []);
      setEditing(false);
      setSel(null);
    })().catch(() => {});
  }, [wf.data, vParam, id]);

  const dirty = !!ir && !!saved && !sameJson(ir, saved);
  const debounced = useDebounced(ir, 450);
  React.useEffect(() => {
    if (!debounced || !editing || !dirty) return;
    api.post<{ issues: Issue[] }>(`/api/workflows/${id}/validate`, { ir: toSubmittable(debounced) }).then((r) => setIssues(r.issues)).catch(() => {});
  }, [debounced, editing, dirty, id]);

  const errors = issues.filter((i) => i.severity === "error");
  const models = React.useMemo(() => {
    const t = settings.data?.models.routerTiers ?? {};
    return [...new Set(Object.values(t))].filter(Boolean);
  }, [settings.data]);
  const tools = catalog.data?.tools ?? [];
  const agents = catalog.data?.agents ?? [];

  const edit = (fn: (x: IR) => IR) => setIr((cur) => (cur ? fn(cur) : cur));
  const selectedNode = sel?.kind === "node" ? ir?.nodes.find((n) => n.id === sel.id) : undefined;
  const selectedEdge = sel?.kind === "edge" ? ir?.edges.find((e) => e.id === sel.id) : undefined;
  const needsRepo = !!ir?.nodes.some((n) => (n.tools ?? []).some((t) => ["RepositoryRead", "CodeSearch", "FileSearch", "PatternScan", "DependencyManifest", "RepositoryWrite", "TestRunner"].includes(t)));

  async function save() {
    if (!ir) return;
    setBusy("save");
    try {
      const v = await api.post<WorkflowVersion>(`/api/workflows/${id}/versions`, { ir: toSubmittable(ir), note: saveNote || null });
      push({ tone: "ok", title: `Saved as v${v.version}`, body: "Versions are immutable. Approve it to make it runnable." });
      setSaveOpen(false); setSaveNote("");
      await wf.reload();
      router.replace(`/workflows/${id}?v=${v.version}`);
    } catch (e) {
      const a = e as ApiError;
      push({ tone: "bad", title: "Couldn't save", body: a.body?.fields?.[0] ? `${a.body.fields[0].loc.join(".")}: ${a.body.fields[0].message}` : a.message });
    } finally { setBusy(null); }
  }
  async function approve() {
    if (!version) return;
    setBusy("approve");
    try {
      await api.post(`/api/workflows/${id}/versions/${version.version}/approve`);
      push({ tone: "ok", title: `v${version.version} approved`, body: "This exact version can now be executed." });
      await wf.reload();
    } catch (e) {
      const a = e as ApiError;
      if (a.body?.issues) { setIssues(a.body.issues); setPanel("validation"); }
      push({ tone: "bad", title: "Can't approve", body: a.message });
    } finally { setBusy(null); }
  }
  async function duplicate() {
    try { const w = await api.post<Workflow>(`/api/workflows/${id}/duplicate`); router.push(`/workflows/${w.id}`); } catch (e) { push({ tone: "bad", title: "Couldn't duplicate", body: (e as ApiError).message }); }
  }
  async function del() {
    try { await api.del(`/api/workflows/${id}`); router.push(`/projects/${wf.data?.projectId}`); } catch (e) { push({ tone: "bad", title: "Couldn't delete", body: (e as ApiError).message }); }
  }

  if (wf.error) return <div className="p-6"><ErrorState error={wf.error} onRetry={wf.reload} title="Workflow not found" /></div>;
  if (!wf.data || !ir || !version) return <div className="p-6"><Skeleton className="h-[70vh] w-full" /></div>;
  const w = wf.data;
  const isLatest = version.version === w.latestVersion?.version;

  return (
    <div className="flex h-full flex-col">
      {/* header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-panel px-5 py-3">
        <div className="min-w-0">
          <p className="text-xs text-faint"><Link href={`/projects/${w.projectId}`} className="hover:text-fg">Project</Link> / <Link href={`/projects/${w.projectId}/workflows`} className="hover:text-fg">Workflows</Link></p>
          <div className="flex items-center gap-2.5">
            <h1 className="truncate text-[17px] font-semibold tracking-tight">{w.name}</h1>
            <select aria-label="Version" value={version.version} onChange={(e) => router.replace(`/workflows/${id}?v=${e.target.value}`)} className="mono h-6 rounded border border-line-strong bg-raised px-1.5 text-xs">
              {w.versions.map((v) => <option key={v.id} value={v.version}>v{v.version}{v.approved ? " ✓" : ""}</option>)}
            </select>
            {version.approved ? <Badge tone="ok"><ShieldCheck className="h-3 w-3" /> approved</Badge> : <Badge tone="warn">draft</Badge>}
            {!isLatest && <Badge>older version</Badge>}
            {dirty && <Badge tone="ember">unsaved changes</Badge>}
            {errors.length > 0 ? <button onClick={() => setPanel("validation")}><Badge tone="bad"><AlertTriangle className="h-3 w-3" />{errors.length} error{errors.length > 1 ? "s" : ""}</Badge></button> : <Badge tone="ok"><CheckCircle2 className="h-3 w-3" />valid</Badge>}
          </div>
          <p className="mt-0.5 max-w-3xl truncate text-sm text-muted" title={ir.goal}>{ir.goal}</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {!editing ? (
            <Button onClick={() => setEditing(true)}><Pencil className="h-3.5 w-3.5" /> Edit</Button>
          ) : (
            <>
              <Button onClick={() => setAddOpen(true)}><Plus className="h-3.5 w-3.5" /> Node</Button>
              <Button variant="ghost" disabled={!dirty} onClick={() => { setIr(structuredClone(saved!)); setIssues(version.issues ?? []); }}><Undo2 className="h-3.5 w-3.5" /> Revert</Button>
              <Button variant="primary" disabled={!dirty} loading={busy === "save"} onClick={() => setSaveOpen(true)}><Save className="h-3.5 w-3.5" /> Save as v{(w.latestVersion?.version ?? 0) + 1}</Button>
            </>
          )}
          <Button onClick={duplicate}><Copy className="h-3.5 w-3.5" /> Duplicate</Button>
          <a href={`/api/workflows/${id}/versions/${version.version}/export`}><Button><Download className="h-3.5 w-3.5" /> Export</Button></a>
          {!version.approved && <Tip content={errors.length ? "Fix validation errors first." : dirty ? "Save your changes first; only saved, immutable versions can be approved." : "Locks this exact version for execution."}><span><Button variant="ok" loading={busy === "approve"} disabled={errors.length > 0 || dirty} onClick={approve}><ShieldCheck className="h-3.5 w-3.5" /> Approve</Button></span></Tip>}
          <Tip content={version.approved ? undefined : "Approve this version before executing."}><span><Button variant="primary" disabled={!version.approved || dirty} onClick={() => setExecOpen(true)}><Play className="h-3.5 w-3.5" /> Execute</Button></span></Tip>
          <Button variant="ghost" size="icon" aria-label="Delete workflow" onClick={() => setDelOpen(true)}><Trash2 className="h-3.5 w-3.5" /></Button>
        </div>
      </div>

      {/* canvas + side panel */}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_400px]">
        <div className="relative min-h-[420px] border-r border-line bg-bg">
          <FlowCanvas
            nodes={ir.nodes} edges={ir.edges} mode="edit" issues={issues}
            selectedNode={sel?.kind === "node" ? sel.id : null} selectedEdge={sel?.kind === "edge" ? sel.id : null}
            onSelectNode={(i) => { setSel(i ? { kind: "node", id: i } : null); if (i) setPanel("inspect"); }}
            onSelectEdge={(i) => { setSel(i ? { kind: "edge", id: i } : null); if (i) setPanel("inspect"); }}
            onConnect={(s, t) => editing && edit((x) => addEdge(x, s, t))}
            onDeleteNodes={(ids) => editing && edit((x) => removeNodes(x, ids.filter((i) => !x.nodes.find((n) => n.id === i)?.locked)))}
            onDeleteEdges={(ids) => editing && edit((x) => ({ ...x, edges: x.edges.filter((e) => !ids.includes(e.id)) }))}
          />
          {!editing && <div className="pointer-events-none absolute left-3 top-3 rounded border border-line bg-panel/90 px-2.5 py-1 text-xs text-faint">Read-only · click Edit to change nodes, wiring, models, tools and budgets</div>}
          {editing && <div className="pointer-events-none absolute left-3 top-3 rounded border border-ember/40 bg-panel/90 px-2.5 py-1 text-xs text-muted">Drag from a node's right handle to another node to connect · Backspace deletes the selection</div>}
        </div>

        <aside className="flex min-h-0 flex-col bg-panel">
          <Tabs value={panel} onChange={setPanel} className="px-2" tabs={[
            { id: "inspect", label: "Inspector" }, { id: "validation", label: "Validation", count: issues.length || null },
            { id: "versions", label: "Versions", count: w.versions.length }, { id: "json", label: "IR" }]} />
          <div className="min-h-0 flex-1 overflow-y-auto">
            {panel === "inspect" && (
              selectedNode ? (
                <NodeInspector node={selectedNode} ir={ir} tools={tools} models={models} readOnly={!editing} issues={issues.filter((i) => i.node_id === selectedNode.id)}
                  onChange={(p) => edit((x) => updateNode(x, selectedNode.id, p))} onDelete={() => { edit((x) => removeNodes(x, [selectedNode.id])); setSel(null); }} />
              ) : selectedEdge ? (
                <EdgeInspector edge={selectedEdge} ir={ir} readOnly={!editing} onChange={(p) => edit((x) => updateEdge(x, selectedEdge.id, p))}
                  onDelete={() => { edit((x) => ({ ...x, edges: x.edges.filter((e) => e.id !== selectedEdge.id) })); setSel(null); }} />
              ) : (
                <WorkflowSettings ir={ir} readOnly={!editing} onChange={(p) => edit((x) => ({ ...x, ...p }))} version={version} />
              )
            )}
            {panel === "validation" && <ValidationList issues={issues} onFocus={(nid) => { setSel({ kind: "node", id: nid }); setPanel("inspect"); }} />}
            {panel === "versions" && (
              <ul className="divide-y divide-line">
                {w.versions.map((v) => (
                  <li key={v.id}>
                    <Link href={`/workflows/${id}?v=${v.version}`} className={cn("flex items-center gap-3 px-4 py-2.5 hover:bg-hover/60", v.version === version.version && "bg-hover/50")}>
                      <span className="mono w-8 text-sm font-medium">v{v.version}</span>
                      <span className="min-w-0 flex-1"><span className="block truncate text-xs text-muted">{v.compilerMeta?.note ?? v.compilerMeta?.source ?? "—"}</span><span className="mono block text-2xs text-faint">{shortId(v.irHash, 10)} · {fmtDateTime(v.createdAt)}</span></span>
                      {v.approved ? <Badge tone="ok">approved</Badge> : <Badge>draft</Badge>}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {panel === "json" && <div className="p-3"><CodeBlock code={JSON.stringify(ir, null, 2)} lang="json" maxHeight={640} /></div>}
          </div>
        </aside>
      </div>

      <AddNodeDialog open={addOpen} onOpenChange={setAddOpen} agents={agents} onAdd={(type, agent, name) => {
        const n = newNode(type, ir.nodes.map((x) => x.id), agent, name);
        edit((x) => ({ ...x, nodes: [...x.nodes, n] }));
        setSel({ kind: "node", id: n.id });
        setPanel("inspect");
        setAddOpen(false);
      }} />
      <Dialog open={saveOpen} onOpenChange={setSaveOpen} title={`Save as v${(w.latestVersion?.version ?? 0) + 1}`} description="Existing versions never change. This creates a new immutable version that needs its own approval."
        footer={<><Button variant="ghost" onClick={() => setSaveOpen(false)}>Cancel</Button><Button variant="primary" loading={busy === "save"} onClick={save}>Save version</Button></>}>
        <Field label="What changed? (optional)"><Input value={saveNote} onChange={(e) => setSaveNote(e.target.value)} maxLength={300} placeholder="e.g. Use Ultra for risk analysis" /></Field>
        {errors.length > 0 && <p className="mt-3 flex gap-1.5 text-xs text-warn"><AlertTriangle className="mt-0.5 h-3 w-3" /> This version has {errors.length} validation error(s). You can save it as a draft, but it can't be approved until they're fixed.</p>}
      </Dialog>
      <Dialog open={delOpen} onOpenChange={setDelOpen} title="Delete workflow?" description="Existing runs keep their frozen configuration."
        footer={<><Button variant="ghost" onClick={() => setDelOpen(false)}>Cancel</Button><Button variant="danger" onClick={del}>Delete</Button></>}><p className="text-sm text-muted">“{w.name}” and its {w.versions.length} version(s) will be removed from your workspace.</p></Dialog>
      <ExecuteDialog open={execOpen} onOpenChange={setExecOpen} versionId={version.id} projectId={w.projectId} goal={ir.goal} needsRepo={needsRepo} />
    </div>
  );
}

function ValidationList({ issues, onFocus }: { issues: Issue[]; onFocus: (nodeId: string) => void }) {
  if (issues.length === 0) return <div className="flex flex-col items-center gap-2 px-6 py-12 text-center"><CheckCircle2 className="h-6 w-6 text-ok" /><p className="text-sm font-medium">No issues</p><p className="text-xs text-faint">No cycles, unreachable nodes, missing inputs, invalid mappings, unknown tools or permission conflicts.</p></div>;
  return (
    <ul className="divide-y divide-line">
      {issues.map((i, k) => (
        <li key={k}>
          <button className="flex w-full items-start gap-2.5 px-4 py-2.5 text-left hover:bg-hover/60" onClick={() => i.node_id && onFocus(i.node_id)}>
            <AlertTriangle className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", i.severity === "error" ? "text-bad" : "text-warn")} />
            <span className="min-w-0"><span className="block text-sm">{i.message}</span><span className="mono text-2xs text-faint">{i.code}{i.node_id ? ` · ${i.node_id}` : ""}{i.edge_id ? ` · ${i.edge_id}` : ""}</span></span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function WorkflowSettings({ ir, readOnly, onChange, version }: { ir: IR; readOnly: boolean; onChange: (p: Partial<IR>) => void; version: WorkflowVersion }) {
  const bp = ir.budgetPolicy ?? {};
  return (
    <div className="space-y-4 p-4">
      <div>
        <p className="text-sm font-semibold">Workflow</p>
        <p className="text-xs text-faint">Select a node or an edge to inspect it.</p>
      </div>
      <Field label="Name"><Input disabled={readOnly} value={ir.name} onChange={(e) => onChange({ name: e.target.value })} /></Field>
      <Field label="Goal"><Textarea disabled={readOnly} rows={4} value={ir.goal} onChange={(e) => onChange({ goal: e.target.value })} /></Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Budget cap (USD)"><Input disabled={readOnly} type="number" min={0} step={0.01} value={bp.maxUsd ?? ""} placeholder="none" onChange={(e) => onChange({ budgetPolicy: { ...bp, maxUsd: e.target.value === "" ? null : Number(e.target.value) } })} /></Field>
        <Field label="Budget cap (tokens)"><Input disabled={readOnly} type="number" min={1} value={bp.maxTokens ?? ""} placeholder="none" onChange={(e) => onChange({ budgetPolicy: { ...bp, maxTokens: e.target.value === "" ? null : Number(e.target.value) } })} /></Field>
      </div>
      <Field label="When a model has no configured price"><Select disabled={readOnly} value={bp.unpricedBehavior ?? "enforce_tokens_only"} onChange={(e) => onChange({ budgetPolicy: { ...bp, unpricedBehavior: e.target.value } })}><option value="enforce_tokens_only">enforce token cap only</option><option value="block">block execution</option></Select></Field>
      <Field label="Max parallel nodes"><Input disabled={readOnly} type="number" min={1} max={32} value={ir.policies?.maxParallelism ?? 4} onChange={(e) => onChange({ policies: { ...ir.policies, maxParallelism: Number(e.target.value) } })} /></Field>
      <Field label="Project memory"><Select disabled={readOnly} value={ir.memoryPolicy?.projectMemory ?? "read"} onChange={(e) => onChange({ memoryPolicy: { ...ir.memoryPolicy, projectMemory: e.target.value } })}><option value="off">off</option><option value="read">read verified facts</option><option value="read_write">read + record verified facts</option></Select></Field>
      <dl className="rounded border border-line bg-raised/40 px-3 py-1">
        <Kv k="Nodes">{ir.nodes.length}</Kv><Kv k="Edges">{ir.edges.length}</Kv>
        <Kv k="IR hash" mono>{shortId(version.irHash, 12)}</Kv><Kv k="Created">{fmtDateTime(version.createdAt)}</Kv>
        {version.compilerMeta?.model && <Kv k="Compiled by" mono>{version.compilerMeta.model}</Kv>}
      </dl>
      <p className="flex items-start gap-1.5 text-xs text-faint"><GitBranchPlus className="mt-0.5 h-3 w-3 shrink-0" /> Editing never changes this version. Saving creates the next immutable version.</p>
    </div>
  );
}

function AddNodeDialog({ open, onOpenChange, agents, onAdd }: { open: boolean; onOpenChange: (o: boolean) => void; agents: Agent[]; onAdd: (t: NodeType, a?: Agent, name?: string) => void }) {
  const [type, setType] = React.useState<NodeType>("AGENT");
  const [agentId, setAgentId] = React.useState("planner");
  const [name, setName] = React.useState("");
  const usable = agents.filter((a) => a.id !== "generalist");
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Add node" description="Every node type has real runtime semantics."
      footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" onClick={() => onAdd(type, type === "AGENT" ? usable.find((a) => a.id === agentId) ?? usable[0] : undefined, name || undefined)}>Add</Button></>}>
      <div className="grid grid-cols-3 gap-2">
        {TYPES.map((t) => {
          const M = NODE_TYPE_META[t];
          return (
            <button key={t} onClick={() => setType(t)} className={cn("rounded border p-2.5 text-left transition-colors", type === t ? "border-ember bg-ember/5" : "border-line hover:bg-hover/60")}>
              <M.icon className={cn("mb-1 h-4 w-4", type === t ? "text-ember" : "text-faint")} />
              <p className="text-sm font-medium">{M.label}</p>
              <p className="text-2xs leading-3 text-faint">{M.hint}</p>
            </button>
          );
        })}
      </div>
      <div className="mt-4 space-y-3">
        {type === "AGENT" && <Field label="Agent"><Select value={agentId} onChange={(e) => setAgentId(e.target.value)}>{usable.map((a) => <option key={a.id} value={a.id}>{a.name}{a.builtin ? "" : " (custom)"}</option>)}</Select></Field>}
        <Field label="Name (optional)"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
      </div>
    </Dialog>
  );
}

export default function WorkflowPage() {
  return (
    <React.Suspense fallback={null}>
      <WorkflowPageInner />
    </React.Suspense>
  );
}
