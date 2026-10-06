"use client";
import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Ban, Download, FileText, Pause, Play, RotateCcw, RotateCw, Radio, TriangleAlert, WifiOff } from "lucide-react";
import { FlowCanvas } from "@/components/flow";
import { TraceFeed } from "@/components/trace";
import { NodeDrawer } from "@/components/node-drawer";
import { ApprovalCard } from "@/components/approval-card";
import { ArtifactInspector, ARTIFACT_LABEL } from "@/components/artifact-inspector";
import { FindingsTable } from "@/components/findings";
import { CostPanel } from "@/components/cost-panel";
import { Failure } from "@/components/failure";
import { Markdown } from "@/components/viz";
import { Money, RunStatusBadge } from "@/components/status";
import { Badge, Button, Empty, ErrorState, Field, Input, Meter, Panel, Select, Skeleton, tableCls } from "@/components/ui/primitives";
import { Dialog, Tabs, Tip, useToast } from "@/components/ui/overlay";
import { ApiError, api } from "@/lib/api";
import { useLiveRun, useNow, useQuery } from "@/lib/hooks";
import { durationBetween, fmtBytes, fmtDuration, fmtInt, fmtRelative, shortId } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ArtifactMeta, IREdge, IRNode, RunNode, VerificationRow } from "@/lib/types";

type Tab = "execution" | "findings" | "report" | "artifacts" | "cost";

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const { run, error, events, connected, active, reload } = useLiveRun(id);
  const now = useNow(active);
  const { push } = useToast();
  const [tab, setTab] = React.useState<Tab>("execution");
  const [node, setNode] = React.useState<string | null>(null);
  const [artifact, setArtifact] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [cancelOpen, setCancelOpen] = React.useState(false);

  // a cheap "version" that changes whenever anything about the run changes: drives refetches of side data
  const version = run ? `${run.status}:${events.length}` : "0";
  const verification = useQuery<{ results: VerificationRow[] }>(`/api/runs/${id}/verification?v=${tab === "findings" ? version : "x"}`);
  const artifacts = useQuery<{ artifacts: ArtifactMeta[] }>(`/api/runs/${id}/artifacts?v=${tab === "artifacts" || tab === "report" ? version : "x"}`);

  if (error) return <div className="p-6"><ErrorState error={error} title="Run not found" /></div>;
  if (!run || !run.nodes || !run.graph) return <div className="space-y-4 p-6"><Skeleton className="h-16" /><Skeleton className="h-[420px]" /></div>;

  const irNodes: IRNode[] = run.graph.nodes.map((n) => ({ id: n.id, type: n.type, name: n.name, agentId: n.agentId ?? undefined }));
  const irEdges: IREdge[] = run.graph.edges.map((e) => ({ id: e.id, source: e.source, target: e.target, kind: e.kind as IREdge["kind"], condition: e.condition }));
  const byId: Record<string, RunNode> = Object.fromEntries(run.nodes.map((n) => [n.id, n]));
  const done = run.nodes.filter((n) => ["SUCCESS", "SKIPPED"].includes(n.state)).length;
  const failed = run.nodes.filter((n) => n.state === "FAILED");
  const elapsed = durationBetween(run.startedAt, run.completedAt, now);
  const budgetBlocked = run.status === "BLOCKED";
  const report = (artifacts.data?.artifacts ?? []).filter((a) => a.type === "report").slice(-1)[0];

  async function control(action: "pause" | "resume" | "cancel" | "retry") {
    setBusy(action);
    try {
      await api.post(`/api/runs/${id}/${action}`);
      await reload();
      push({ tone: "ok", title: { pause: "Pause requested: running steps stop at their next checkpoint", resume: "Resumed", cancel: "Run cancelled", retry: "Retrying failed steps (successful steps are reused)" }[action] });
    } catch (e) {
      push({ tone: "bad", title: "Action failed", body: (e as ApiError).message });
    } finally { setBusy(null); setCancelOpen(false); }
  }

  const terminal = ["SUCCESS", "FAILED", "CANCELLED"].includes(run.status);
  return (
    <div className="flex min-h-full flex-col">
      {/* RUN STATUS */}
      <div className="border-b border-line bg-panel px-5 py-3">
        <p className="text-xs text-faint"><Link href="/runs" className="hover:text-fg">Runs</Link> / <span className="mono">{shortId(run.id)}</span>
          {run.parentRunId && <> · replay of <Link className="mono text-info hover:underline" href={`/runs/${run.parentRunId}`}>{shortId(run.parentRunId)}</Link></>}</p>
        <div className="mt-0.5 flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <h1 className="truncate text-[17px] font-semibold tracking-tight">{run.workflowName}</h1>
            {run.workflowId && <Link href={`/workflows/${run.workflowId}?v=${run.version}`} className="mono rounded border border-line-strong px-1.5 text-xs text-muted hover:text-fg">v{run.version}</Link>}
            <RunStatusBadge status={run.status} />
            {run.replayConfig?.model && <Badge tone="info" className="normal-case">model: {run.replayConfig.model}</Badge>}
            <Tip content={connected ? "Live: events stream from the server as they happen" : terminal ? "Run finished: showing the recorded trace" : "Reconnecting to the event stream…"}>
              <span className={cn("inline-flex items-center gap-1 text-xs", connected ? "text-ok" : "text-faint")}>{connected ? <Radio className="h-3.5 w-3.5" /> : !terminal ? <WifiOff className="h-3.5 w-3.5" /> : null}{connected ? "live" : ""}</span>
            </Tip>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {["RUNNING", "WAITING_APPROVAL", "BLOCKED"].includes(run.status) && <Button loading={busy === "pause"} onClick={() => control("pause")}><Pause className="h-3.5 w-3.5" /> Pause</Button>}
            {run.status === "PAUSED" && <Button variant="primary" loading={busy === "resume"} onClick={() => control("resume")}><Play className="h-3.5 w-3.5" /> Resume</Button>}
            {run.status === "FAILED" && <Button variant="primary" loading={busy === "retry"} onClick={() => control("retry")}><RotateCw className="h-3.5 w-3.5" /> Retry failed steps</Button>}
            {!terminal && <Button variant="danger" onClick={() => setCancelOpen(true)}><Ban className="h-3.5 w-3.5" /> Cancel</Button>}
            <Link href={`/runs/${id}/replay`}><Button><RotateCcw className="h-3.5 w-3.5" /> Replay</Button></Link>
            <ExportMenu runId={id} hasReport={!!report || run.status === "SUCCESS"} />
          </div>
        </div>
        <p className="mt-1 max-w-4xl truncate text-sm text-muted" title={run.input?.objective}>{run.input?.objective ?? run.goal}</p>
        <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-3 xl:grid-cols-6">
          <Stat label="Progress"><span className="num">{done}/{run.nodes.length}</span><span className="text-faint"> nodes</span></Stat>
          <Stat label="Duration"><span className="num">{fmtDuration(elapsed)}</span></Stat>
          <Stat label="Tokens"><span className="num">{fmtInt(run.totals.totalTokens)}</span><span className="text-faint"> · {run.totals.modelCalls} calls</span></Stat>
          <Stat label="Cost"><Money value={run.totals.costUsd} basis={run.totals.costBasis} partial={run.totals.costPartial} /></Stat>
          <Stat label="Retries / violations"><span className="num">{run.totals.retries}</span><span className="text-faint"> / </span><span className={cn("num", run.totals.policyViolations && "text-warn")}>{run.totals.policyViolations}</span></Stat>
          <Stat label="Budget">
            {run.budget.maxUsd !== null ? <span className="block"><span className="num text-xs">${run.budget.spentUsd.toFixed(4)} / ${run.budget.maxUsd}</span><Meter value={run.budget.spentUsd} max={run.budget.maxUsd} label="USD budget" /></span>
              : run.budget.maxTokens !== null ? <span className="block"><span className="num text-xs">{fmtInt(run.budget.spentTokens)} / {fmtInt(run.budget.maxTokens)} tok</span><Meter value={run.budget.spentTokens} max={run.budget.maxTokens} label="Token budget" /></span>
              : <span className="text-faint">no cap</span>}
          </Stat>
        </div>
      </div>

      <div className="flex-1 space-y-4 p-5">
        {run.pendingApprovals.map((a) => <ApprovalCard key={a.id} approval={a} onDone={reload} />)}
        {budgetBlocked && <BudgetBlocked runId={id} run={run} onDone={reload} />}
        {run.status === "FAILED" && failed[0]?.error && (
          <div className="grid gap-3 lg:grid-cols-2">{failed.slice(0, 2).map((f) => <Failure key={f.id} nodeName={f.name} error={f.error!} />)}</div>
        )}

        <Tabs value={tab} onChange={setTab} tabs={[
          { id: "execution", label: "Execution" }, { id: "findings", label: "Findings", count: verification.data?.results.length || null },
          { id: "report", label: "Report" }, { id: "artifacts", label: "Artifacts", count: artifacts.data?.artifacts.length || null }, { id: "cost", label: "Cost & tokens" }]} />

        {tab === "execution" && (
          <>
            <Panel flush className="overflow-hidden">
              <div className="h-[420px] bg-bg">
                <FlowCanvas nodes={irNodes} edges={irEdges} mode="run" runNodes={byId} now={now} selectedNode={node} onSelectNode={(n) => n && setNode(n)} />
              </div>
            </Panel>
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
              <Panel title="Execution trace" subtitle={`${events.length} events · append-only, persisted`} flush>
                <TraceFeed events={events} startedAt={run.startedAt} onNode={setNode} height={380} />
              </Panel>
              <Panel title="Steps" flush>
                <ul className="max-h-[420px] divide-y divide-line overflow-y-auto">
                  {run.nodes.map((n) => (
                    <li key={n.id}>
                      <button onClick={() => setNode(n.id)} className="flex w-full items-center gap-2.5 px-3.5 py-2 text-left hover:bg-hover/60">
                        <span className={cn("h-2 w-2 shrink-0 rounded-full", { SUCCESS: "bg-ok", FAILED: "bg-bad", RUNNING: "animate-pulseRing bg-ember", WAITING_APPROVAL: "bg-warn", BLOCKED: "bg-bad", RETRYING: "bg-warn", READY: "bg-info" }[n.state as string] ?? "bg-line-strong")} />
                        <span className="min-w-0 flex-1"><span className="block truncate text-sm">{n.name}</span><span className="mono block truncate text-2xs text-faint">{n.model ?? n.type?.toLowerCase()}</span></span>
                        <span className="num shrink-0 text-xs text-faint">{n.startedAt ? fmtDuration(durationBetween(n.startedAt, n.finishedAt, now)) : n.state === "SKIPPED" ? "skipped" : "—"}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </Panel>
            </div>
          </>
        )}
        {tab === "findings" && <Panel flush>{verification.data ? <FindingsTable rows={verification.data.results} /> : <Skeleton className="m-3 h-32" />}</Panel>}
        {tab === "report" && (
          <Panel title="Report" flush actions={report && <a href={`/api/runs/${id}/export?format=markdown`}><Button size="xs"><Download className="h-3 w-3" /> Markdown</Button></a>}>
            {report ? <ReportBody artifactId={report.id} /> : <Empty icon={<FileText className="h-6 w-6" />} title="No report yet">The report is written from verified results once the earlier steps finish.</Empty>}
          </Panel>
        )}
        {tab === "artifacts" && (
          <Panel flush>
            {!artifacts.data ? <Skeleton className="m-3 h-24" /> : artifacts.data.artifacts.length === 0 ? <Empty title="No artifacts yet" /> : (
              <table className={tableCls.table}>
                <thead><tr><th className={tableCls.th}>Type</th><th className={tableCls.th}>Node</th><th className={tableCls.th}>Producer</th><th className={tableCls.th}>Hash</th><th className={cn(tableCls.th, "text-right")}>Size</th><th className={cn(tableCls.th, "text-right")}>Created</th></tr></thead>
                <tbody>{artifacts.data.artifacts.map((a) => (
                  <tr key={a.id} className={cn(tableCls.tr, "cursor-pointer")} onClick={() => setArtifact(a.id)}>
                    <td className={tableCls.td}><span className="font-medium text-info">{ARTIFACT_LABEL[a.type] ?? a.type}</span></td>
                    <td className={cn(tableCls.td, "mono text-xs")}>{a.nodeId}</td><td className={cn(tableCls.td, "mono text-xs text-muted")}>{a.provenance?.producer ?? a.provenance?.tool}</td>
                    <td className={cn(tableCls.td, "mono text-xs text-faint")}>{a.contentHash.slice(0, 12)}</td><td className={cn(tableCls.td, "num text-right")}>{fmtBytes(a.sizeBytes)}</td>
                    <td className={cn(tableCls.td, "num text-right text-muted")}>{fmtRelative(a.createdAt)}</td>
                  </tr>))}</tbody>
              </table>
            )}
          </Panel>
        )}
        {tab === "cost" && <Panel flush><CostPanel run={run} version={version} /></Panel>}
      </div>

      <NodeDrawer runId={id} nodeId={node} version={version} onClose={() => setNode(null)} onArtifact={(a) => { setNode(null); setArtifact(a); }} />
      <ArtifactInspector id={artifact} onClose={() => setArtifact(null)} />
      <Dialog open={cancelOpen} onOpenChange={setCancelOpen} title="Cancel this run?" description="Running steps stop at their next checkpoint; queued steps never start. This can't be undone (you can replay it later)."
        footer={<><Button variant="ghost" onClick={() => setCancelOpen(false)}>Keep running</Button><Button variant="danger" loading={busy === "cancel"} onClick={() => control("cancel")}>Cancel run</Button></>}>
        <p className="text-sm text-muted">{run.nodes.length - done} step(s) have not finished.</p>
      </Dialog>
    </div>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="min-w-0"><p className="text-xs text-faint">{label}</p><div className="mt-0.5 truncate">{children}</div></div>;
}

function ReportBody({ artifactId }: { artifactId: string }) {
  const q = useQuery<ArtifactMeta>(`/api/artifacts/${artifactId}`);
  if (!q.data) return <Skeleton className="m-4 h-40" />;
  const c = q.data.content as { markdown?: string; title?: string } | string;
  return <div className="max-w-4xl px-6 py-5"><Markdown>{typeof c === "string" ? c : c?.markdown ?? ""}</Markdown></div>;
}

function ExportMenu({ runId, hasReport }: { runId: string; hasReport: boolean }) {
  const [open, setOpen] = React.useState(false);
  const items: [string, string][] = [["json", "Run (JSON)"], ["bundle", "Run + artifacts (ZIP)"], ["trace", "Execution trace"], ...(hasReport ? [["markdown", "Security report (Markdown)"] as [string, string]] : [])];
  return (
    <div className="relative">
      <Button onClick={() => setOpen(!open)} aria-expanded={open}><Download className="h-3.5 w-3.5" /> Export</Button>
      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-30 mt-1 w-56 overflow-hidden rounded-md border border-line-strong bg-raised shadow-xl">
            {items.map(([f, l]) => <a key={f} href={`/api/runs/${runId}/export?format=${f}`} onClick={() => setOpen(false)} className="block px-3 py-2 text-sm hover:bg-hover">{l}</a>)}
          </div>
        </>
      )}
    </div>
  );
}

function BudgetBlocked({ runId, run, onDone }: { runId: string; run: NonNullable<ReturnType<typeof useLiveRun>["run"]>; onDone: () => void }) {
  const settings = useQuery<{ models: { routerTiers: Record<string, string> } }>("/api/settings");
  const [usd, setUsd] = React.useState(run.budget.maxUsd !== null ? String(Math.round((run.budget.maxUsd * 2) * 100) / 100) : "");
  const [tok, setTok] = React.useState(run.budget.maxTokens !== null ? String(run.budget.maxTokens * 2) : "");
  const [model, setModel] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const { push } = useToast();
  const blocked = run.nodes?.filter((n) => n.state === "BLOCKED" && n.blockedReason === "budget") ?? [];
  const reason = blocked[0]?.error?.message;
  async function go(action: "increase_budget" | "retry_with_cheaper_model") {
    setBusy(true);
    try {
      await api.post(`/api/runs/${runId}/unblock`, action === "increase_budget"
        ? { action, max_usd: usd ? Number(usd) : null, max_tokens: tok ? Number(tok) : null }
        : { action, model });
      onDone();
    } catch (e) { push({ tone: "bad", title: "Couldn't unblock", body: (e as ApiError).message }); } finally { setBusy(false); }
  }
  const models = [...new Set(Object.values(settings.data?.models.routerTiers ?? {}))];
  return (
    <section className="rounded-md border border-warn/50 bg-warn/[0.04] p-4" aria-label="Budget block">
      <p className="flex items-center gap-2 text-sm font-semibold text-warn"><TriangleAlert className="h-4 w-4" /> Execution BLOCKED by budget</p>
      <p className="mt-1 text-sm text-muted">{reason ?? "The next model call would exceed the budget."} The blocked call was never made.</p>
      <div className="mt-3 grid gap-4 md:grid-cols-[1fr_1fr_auto] md:items-end">
        <div className="grid grid-cols-2 gap-2">
          <Field label="New USD cap"><Input type="number" min={0} step={0.01} value={usd} onChange={(e) => setUsd(e.target.value)} placeholder="unchanged" /></Field>
          <Field label="New token cap"><Input type="number" min={1} value={tok} onChange={(e) => setTok(e.target.value)} placeholder="unchanged" /></Field>
        </div>
        <Field label="…or re-run blocked steps on a cheaper model"><Select value={model} onChange={(e) => setModel(e.target.value)}><option value="">Choose model</option>{models.map((m) => <option key={m}>{m}</option>)}</Select></Field>
        <div className="flex gap-2">
          <Button variant="primary" loading={busy} disabled={!usd && !tok} onClick={() => go("increase_budget")}>Increase budget</Button>
          <Button loading={busy} disabled={!model} onClick={() => go("retry_with_cheaper_model")}>Use cheaper model</Button>
        </div>
      </div>
    </section>
  );
}
