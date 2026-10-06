"use client";
import * as React from "react";
import { CheckCircle2, KeyRound, TriangleAlert } from "lucide-react";
import { Badge, Button, ErrorState, Field, Input, Kv, PageHeader, Panel, Skeleton, tableCls } from "@/components/ui/primitives";
import { Tabs, useToast } from "@/components/ui/overlay";
import { ApiError, api } from "@/lib/api";
import { useQuery } from "@/lib/hooks";
import { useAuth } from "@/lib/auth";
import { fmtDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

interface SettingsView {
  environment: string; role: string;
  provider: { name: string; baseUrlHost: string; apiKeyConfigured: boolean };
  models: { nano: string | null; super: string | null; ultra: string | null; default: string | null; routerTiers: Record<string, string> };
  pricing: { model: string; inputPerMtok: number; outputPerMtok: number; source: string }[];
  tavilyConfigured: boolean; sandbox: { enabled: boolean; dockerAvailable: boolean; image: string }; storage: { driver: string };
  retention: { run_logs_days: number; artifacts_days: number; memory_days: number; traces_days: number } | null;
  githubOAuthConfigured: boolean; limits: { maxZipMB: number; maxRepoFiles: number };
}
interface AuditEntry { id: number; userId: string | null; action: string; targetType: string | null; targetId: string | null; metadata: Record<string, any>; ts: string }

function Configured({ ok, missing }: { ok: boolean; missing: string }) {
  return ok ? <span className="inline-flex items-center gap-1 text-ok"><CheckCircle2 className="h-3.5 w-3.5" /> configured</span> : <span className="inline-flex items-center gap-1 text-warn"><TriangleAlert className="h-3.5 w-3.5" /> {missing}</span>;
}

export default function SettingsPage() {
  const q = useQuery<SettingsView>("/api/settings");
  const audit = useQuery<{ entries: AuditEntry[] }>("/api/audit-log?limit=150");
  const { me, workspace } = useAuth();
  const [tab, setTab] = React.useState<"models" | "retention" | "audit" | "workspace">("models");
  const [ret, setRet] = React.useState<SettingsView["retention"]>(null);
  const [busy, setBusy] = React.useState(false);
  const { push } = useToast();
  React.useEffect(() => { if (q.data?.retention) setRet(q.data.retention); }, [q.data]);
  async function saveRetention() {
    setBusy(true);
    try { await api.put("/api/settings/retention", ret); push({ tone: "ok", title: "Retention policy saved" }); } catch (e) { push({ tone: "bad", title: "Couldn't save", body: (e as ApiError).message }); } finally { setBusy(false); }
  }
  if (q.error) return <div className="p-6"><ErrorState error={q.error} onRetry={q.reload} /></div>;
  const s = q.data;
  return (
    <div className="mx-auto max-w-[1000px] p-6">
      <PageHeader title="Settings" subtitle="Secrets live only in server environment variables. This page shows whether they are set, never their values." />
      <Tabs value={tab} onChange={setTab} className="mb-4" tabs={[{ id: "models", label: "Models & providers" }, { id: "retention", label: "Data retention" }, { id: "audit", label: "Audit log" }, { id: "workspace", label: "Workspace" }]} />
      {!s ? <Skeleton className="h-64" /> : (
        <>
          {tab === "models" && (
            <div className="space-y-4">
              <Panel title="Nebius Token Factory" subtitle="All inference runs here, on NVIDIA Nemotron open models.">
                <dl>
                  <Kv k="API key"><Configured ok={s.provider.apiKeyConfigured} missing="missing: set NEBIUS_API_KEY on the server" /></Kv>
                  <Kv k="Endpoint" mono>{s.provider.baseUrlHost}</Kv>
                  <Kv k="Nano tier (fast, routine)" mono>{s.models.nano ?? <span className="text-faint">not set (NEBIUS_MODEL_NANO)</span>}</Kv>
                  <Kv k="Super tier (balanced)" mono>{s.models.super ?? <span className="text-faint">not set (NEBIUS_MODEL_SUPER)</span>}</Kv>
                  <Kv k="Ultra tier (hard reasoning, verification)" mono>{s.models.ultra ?? <span className="text-faint">not set (NEBIUS_MODEL_ULTRA)</span>}</Kv>
                </dl>
                <p className="mt-3 text-xs text-faint">Routing is deterministic and explainable: task complexity + risk (+ verification criticality) picks a tier, adjusted for latency, budget and availability. Each node shows why it got its model.</p>
              </Panel>
              <Panel title="Model pricing" subtitle="Used to compute cost from provider-reported tokens." flush>
                {s.pricing.length === 0 ? (
                  <p className="px-4 py-5 text-sm text-muted"><TriangleAlert className="mr-1.5 inline h-4 w-4 text-warn" />No prices configured, so cost is shown as <b>Not available</b> everywhere. FORGE never guesses. Add per-million-token prices from your Token Factory console to <span className="mono">backend/forge/providers/pricing.json</span> (or <span className="mono">FORGE_PRICING_FILE</span>).</p>
                ) : (
                  <table className={tableCls.table}>
                    <thead><tr><th className={tableCls.th}>Model</th><th className={cn(tableCls.th, "text-right")}>Input / 1M</th><th className={cn(tableCls.th, "text-right")}>Output / 1M</th><th className={tableCls.th}>Source</th></tr></thead>
                    <tbody>{s.pricing.map((p) => <tr key={p.model} className={tableCls.tr}><td className={cn(tableCls.td, "mono text-xs")}>{p.model}</td><td className={cn(tableCls.td, "num text-right")}>${p.inputPerMtok}</td><td className={cn(tableCls.td, "num text-right")}>${p.outputPerMtok}</td><td className={cn(tableCls.td, "text-muted")}>{p.source || "—"}</td></tr>)}</tbody>
                  </table>
                )}
              </Panel>
              <Panel title="Tools & infrastructure">
                <dl>
                  <Kv k="Tavily (web research)"><Configured ok={s.tavilyConfigured} missing="not set: research steps report the limitation" /></Kv>
                  <Kv k="Test sandbox">{s.sandbox.enabled && s.sandbox.dockerAvailable ? <span className="text-ok">Docker · {s.sandbox.image} · no network</span> : <span className="text-warn">{s.sandbox.enabled ? "Docker unreachable: tests report NOT_AVAILABLE" : "disabled"}</span>}</Kv>
                  <Kv k="Object storage">{s.storage.driver === "s3" ? "S3-compatible (Nebius Object Storage)" : "local filesystem"}</Kv>
                  <Kv k="GitHub OAuth"><Configured ok={s.githubOAuthConfigured} missing="not set: public repos + per-import tokens only" /></Kv>
                  <Kv k="Upload limits">{s.limits.maxZipMB} MB ZIP · {s.limits.maxRepoFiles.toLocaleString()} files</Kv>
                  <Kv k="Environment"><Badge>{s.environment}</Badge></Kv>
                </dl>
              </Panel>
            </div>
          )}
          {tab === "retention" && ret && (
            <Panel title="Data retention" subtitle="A sweeper on the worker deletes data of finished runs older than these limits. Nothing is kept indefinitely by accident.">
              <div className="grid gap-4 sm:grid-cols-2">
                {([["run_logs_days", "Run logs (events)"], ["traces_days", "Execution traces (tool calls, checkpoints)"], ["artifacts_days", "Artifacts (and their stored objects)"], ["memory_days", "Project memory"]] as const).map(([k, l]) => (
                  <Field key={k} label={l} hint="days"><Input type="number" min={1} max={3650} value={ret[k]} disabled={s.role !== "owner" && s.role !== "admin"} onChange={(e) => setRet({ ...ret, [k]: Number(e.target.value) })} /></Field>
                ))}
              </div>
              <div className="mt-4 flex items-center justify-between">
                <p className="text-xs text-faint">{s.role === "owner" || s.role === "admin" ? "Changes are recorded in the audit log." : "Only owners and admins can change retention."}</p>
                <Button variant="primary" loading={busy} disabled={s.role !== "owner" && s.role !== "admin"} onClick={saveRetention}>Save</Button>
              </div>
            </Panel>
          )}
          {tab === "audit" && (
            <Panel title="Audit log" subtitle="Append-only (enforced by a database trigger)." flush>
              {!audit.data ? <Skeleton className="m-3 h-24" /> : (
                <table className={tableCls.table}>
                  <thead><tr><th className={tableCls.th}>When</th><th className={tableCls.th}>Action</th><th className={tableCls.th}>Target</th><th className={tableCls.th}>Details</th></tr></thead>
                  <tbody>{audit.data.entries.map((e) => (
                    <tr key={e.id} className={tableCls.tr}>
                      <td className={cn(tableCls.td, "num whitespace-nowrap text-muted")}>{fmtDateTime(e.ts)}</td><td className={cn(tableCls.td, "mono text-xs")}>{e.action}</td>
                      <td className={cn(tableCls.td, "mono text-xs text-muted")}>{e.targetType ? `${e.targetType}:${(e.targetId ?? "").slice(0, 8)}` : "—"}</td>
                      <td className={cn(tableCls.td, "mono max-w-[360px] truncate text-xs text-faint")}>{Object.keys(e.metadata).length ? JSON.stringify(e.metadata) : ""}</td>
                    </tr>))}</tbody>
                </table>
              )}
            </Panel>
          )}
          {tab === "workspace" && (
            <Panel title="Workspace">
              <dl>
                <Kv k="Name">{workspace?.name}</Kv><Kv k="Your role">{workspace?.role}</Kv><Kv k="Signed in as">{me?.user.email}</Kv>
                <Kv k="Workspace id" mono>{workspace?.id}</Kv>
              </dl>
              <p className="mt-3 flex items-start gap-1.5 text-xs text-faint"><KeyRound className="mt-0.5 h-3 w-3 shrink-0" />Every resource is scoped to the workspace on the server. The workspace selector only chooses among workspaces you are a member of; the API re-checks membership on every request.</p>
              <p className="mt-2 text-xs text-faint">Generated {fmtDateTime(new Date().toISOString())}</p>
            </Panel>
          )}
        </>
      )}
    </div>
  );
}
