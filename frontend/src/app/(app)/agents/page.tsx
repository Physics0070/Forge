"use client";
import * as React from "react";
import { Bot, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { Badge, Button, ErrorState, Field, Input, PageHeader, Panel, Select, Skeleton, Textarea, tableCls } from "@/components/ui/primitives";
import { Dialog, Drawer, Tabs, useToast } from "@/components/ui/overlay";
import { CodeBlock, JsonTree } from "@/components/viz";
import { ApiError, api } from "@/lib/api";
import { useQuery } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { Agent, ToolDef } from "@/lib/types";

interface Catalog { agents: Agent[]; tools: ToolDef[]; permissions: Record<string, string> }

export default function AgentsPage() {
  const q = useQuery<Catalog>("/api/agents");
  const [tab, setTab] = React.useState<"agents" | "tools" | "permissions">("agents");
  const [sel, setSel] = React.useState<Agent | null>(null);
  const [open, setOpen] = React.useState(false);
  const { push } = useToast();
  async function remove(a: Agent) {
    try { await api.del(`/api/agents/${a.id}`); setSel(null); q.reload(); } catch (e) { push({ tone: "bad", title: "Couldn't delete", body: (e as ApiError).message }); }
  }
  return (
    <div className="mx-auto max-w-[1180px] p-6">
      <PageHeader title="Agents & tools" subtitle="Agents are declarative: a contract, typed input/output, the tools they may use, and the permissions those tools need. Every tool call is authorised against this before it runs."
        actions={<Button variant="primary" onClick={() => setOpen(true)}><Plus className="h-3.5 w-3.5" /> Custom agent</Button>} />
      <Tabs value={tab} onChange={setTab} className="mb-4" tabs={[{ id: "agents", label: "Agents", count: q.data?.agents.length }, { id: "tools", label: "Tools", count: q.data?.tools.length }, { id: "permissions", label: "Permissions" }]} />
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} /> : !q.data ? <Skeleton className="h-64" /> : (
        <>
          {tab === "agents" && (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {q.data.agents.map((a) => (
                <button key={a.id} onClick={() => setSel(a)} className="flex flex-col rounded-md border border-line bg-panel p-4 text-left transition-colors hover:border-line-strong hover:bg-raised">
                  <div className="flex items-center gap-2"><Bot className="h-4 w-4 text-ember" /><span className="font-medium">{a.name}</span>{!a.builtin && <Badge tone="info">custom</Badge>}</div>
                  <p className="mono mt-0.5 text-xs text-faint">{a.id}</p>
                  <p className="mt-2 line-clamp-2 text-sm text-muted">{a.description}</p>
                  <div className="mt-3 flex flex-wrap gap-1">
                    {a.tools.length === 0 ? <span className="text-xs text-faint">no tools: reasons over its input only</span> : a.tools.map((t) => <span key={t} className="mono rounded bg-raised px-1.5 py-0.5 text-2xs text-muted">{t}</span>)}
                  </div>
                  <div className="mt-auto flex items-center gap-1.5 pt-3 text-2xs text-faint"><ShieldCheck className="h-3 w-3" />{a.permissions.length ? a.permissions.join(" · ") : "no permissions"}</div>
                </button>
              ))}
            </div>
          )}
          {tab === "tools" && (
            <Panel flush>
              <table className={tableCls.table}>
                <thead><tr><th className={tableCls.th}>Tool</th><th className={tableCls.th}>What it does</th><th className={tableCls.th}>Requires</th><th className={tableCls.th}>Side effects</th></tr></thead>
                <tbody>{q.data.tools.map((t) => (
                  <tr key={t.id} className={tableCls.tr}>
                    <td className={cn(tableCls.td, "mono text-xs font-medium")}>{t.id}</td><td className={cn(tableCls.td, "text-muted")}>{t.description}</td>
                    <td className={cn(tableCls.td, "mono text-xs")}>{t.permissions.join(", ")}</td>
                    <td className={tableCls.td}>{t.requiresApproval ? <Badge tone="warn">approval required</Badge> : t.sideEffects === "none" ? <span className="text-faint">read-only</span> : <Badge>{t.sideEffects.replace("_", " ")}</Badge>}</td>
                  </tr>))}</tbody>
              </table>
            </Panel>
          )}
          {tab === "permissions" && (
            <Panel flush>
              <table className={tableCls.table}><tbody>{Object.entries(q.data.permissions).map(([k, v]) => (
                <tr key={k} className={tableCls.tr}><td className={cn(tableCls.td, "mono w-56 text-xs")}>{k}</td><td className={cn(tableCls.td, "text-muted")}>{v}</td></tr>))}</tbody></table>
            </Panel>
          )}
        </>
      )}
      <Drawer open={!!sel} onClose={() => setSel(null)} title={sel?.name ?? ""} subtitle={sel ? `${sel.id} · ${sel.builtin ? "built-in" : "custom"}` : undefined}>
        {sel && (
          <div className="space-y-4 p-4">
            <p className="text-sm text-muted">{sel.description}</p>
            <div className="flex flex-wrap gap-1.5 text-xs">{Object.entries(sel.routing).map(([k, v]) => <Badge key={k} className="normal-case">{k}: {String(v)}</Badge>)}<Badge className="normal-case">timeout {sel.timeoutS}s</Badge><Badge className="normal-case">model: {sel.model ?? "routed"}</Badge></div>
            <div><p className="mb-1 text-xs font-medium text-muted">System contract</p><CodeBlock code={sel.systemContract} maxHeight={260} /></div>
            <div><p className="mb-1 text-xs font-medium text-muted">Input schema</p><JsonTree data={sel.inputSchema} maxHeight={200} /></div>
            <div><p className="mb-1 text-xs font-medium text-muted">Output schema</p><JsonTree data={sel.outputSchema} maxHeight={240} /></div>
            {!sel.builtin && <Button variant="danger" onClick={() => remove(sel)}><Trash2 className="h-3.5 w-3.5" /> Delete agent</Button>}
          </div>
        )}
      </Drawer>
      {q.data && <NewAgent open={open} onOpenChange={setOpen} tools={q.data.tools} permissions={Object.keys(q.data.permissions)} onCreated={q.reload} />}
    </div>
  );
}

function NewAgent({ open, onOpenChange, tools, permissions, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; tools: ToolDef[]; permissions: string[]; onCreated: () => void }) {
  const [id, setId] = React.useState("");
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [contract, setContract] = React.useState("ROLE: …\nDescribe exactly what this agent must do and what it must return.");
  const [sel, setSel] = React.useState<string[]>([]);
  const [perms, setPerms] = React.useState<string[]>([]);
  const [out, setOut] = React.useState('{\n  "type": "object",\n  "properties": { "summary": { "type": "string" } },\n  "required": ["summary"]\n}');
  const [complexity, setComplexity] = React.useState("medium");
  const [err, setErr] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const need = [...new Set(sel.flatMap((t) => tools.find((x) => x.id === t)?.permissions ?? []))];
  React.useEffect(() => setPerms((p) => [...new Set([...p, ...need])]), [sel.join()]); // eslint-disable-line react-hooks/exhaustive-deps
  async function create() {
    setErr(null);
    let schema: unknown;
    try { schema = JSON.parse(out); } catch { setErr("Output schema is not valid JSON."); return; }
    setBusy(true);
    try {
      await api.post("/api/agents", { id, name, description, systemContract: contract, tools: sel, permissions: perms, outputSchema: schema, routing: { complexity } });
      onOpenChange(false);
      onCreated();
    } catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Custom agent" width="max-w-2xl" description="The FORGE safety preamble (untrusted-data handling, no secrets, no invented evidence) is always prepended to your contract."
      footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" loading={busy} disabled={!id || !name || contract.length < 20} onClick={create}>Create agent</Button></>}>
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Id" hint="lowercase, digits, _"><Input className="mono" value={id} onChange={(e) => setId(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))} placeholder="license_checker" /></Field>
          <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="License Checker" /></Field>
        </div>
        <Field label="Description"><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label="Contract"><Textarea rows={5} className="mono text-xs" value={contract} onChange={(e) => setContract(e.target.value)} /></Field>
        <Field label="Tools">
          <div className="flex flex-wrap gap-1.5">{tools.map((t) => {
            const on = sel.includes(t.id);
            return <button key={t.id} type="button" onClick={() => setSel(on ? sel.filter((x) => x !== t.id) : [...sel, t.id])} className={cn("mono rounded border px-1.5 py-0.5 text-xs", on ? "border-ember/60 bg-ember/10 text-ember" : "border-line-strong text-muted")}>{t.id}</button>;
          })}</div>
        </Field>
        <Field label="Permissions" hint="Tools' required permissions are added automatically.">
          <div className="flex flex-wrap gap-1.5">{permissions.map((p) => {
            const on = perms.includes(p);
            return <button key={p} type="button" onClick={() => !need.includes(p) && setPerms(on ? perms.filter((x) => x !== p) : [...perms, p])} className={cn("mono rounded border px-1.5 py-0.5 text-xs", on ? "border-ok/50 bg-ok/10 text-ok" : "border-line-strong text-muted", need.includes(p) && "cursor-default")}>{p}</button>;
          })}</div>
        </Field>
        <div className="grid grid-cols-[1fr_160px] gap-3">
          <Field label="Output schema (JSON Schema)"><Textarea rows={6} className="mono text-xs" value={out} onChange={(e) => setOut(e.target.value)} /></Field>
          <Field label="Complexity (routing)"><Select value={complexity} onChange={(e) => setComplexity(e.target.value)}><option>low</option><option>medium</option><option>high</option></Select></Field>
        </div>
        {err && <p className="rounded border border-bad/30 bg-bad/5 px-3 py-2 text-sm text-bad">{err}</p>}
      </div>
    </Dialog>
  );
}
