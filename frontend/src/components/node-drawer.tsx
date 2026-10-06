"use client";
import * as React from "react";
import { Drawer, Tabs } from "@/components/ui/overlay";
import { Badge, ErrorState, Kv, Skeleton, tableCls } from "@/components/ui/primitives";
import { Money, NODE_TYPE_META, NodeStateBadge, VerificationBadge } from "@/components/status";
import { Failure } from "@/components/failure";
import { describe } from "@/components/trace";
import { JsonTree } from "@/components/viz";
import { useQuery } from "@/lib/hooks";
import { fmtDateTime, fmtDuration, fmtMs, fmtTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { CostBasis, ForgeEvent, RunNode, VStatus } from "@/lib/types";

interface NodeDetail extends RunNode {
  definition: any;
  modelCalls: { id: string; purpose: string; model: string; status: string; errorClass: string | null; errorMessage: string | null; inputTokens: number | null; outputTokens: number | null; latencyMs: number | null; costUsd: number | null; costBasis: CostBasis; ts: string }[];
  toolCalls: { id: string; tool: string; operation: string; decision: "ALLOW" | "DENY"; reason: string; status: string; error: string | null; input: any; summary: any; durationMs: number | null; ts: string }[];
  artifacts: { id: string; type: string }[];
  verification: { findingId: string; status: VStatus; confidence: number; evidenceScore: number; reason: string; missingEvidence: string[]; recommendations: string[]; checks: Record<string, any> }[];
  checkpoints: { state: string; ts: string }[];
  events: ForgeEvent[];
}

export function NodeDrawer({ runId, nodeId, version, onClose, onArtifact }: { runId: string; nodeId: string | null; version: string; onClose: () => void; onArtifact: (id: string) => void }) {
  const q = useQuery<NodeDetail>(nodeId ? `/api/runs/${runId}/nodes/${nodeId}?v=${version}` : null);
  const [tab, setTab] = React.useState<"overview" | "io" | "model" | "tools" | "verify" | "events">("overview");
  React.useEffect(() => setTab("overview"), [nodeId]);
  const n = q.data;
  const M = n?.type ? NODE_TYPE_META[n.type] : null;
  return (
    <Drawer open={!!nodeId} onClose={onClose} width="w-[620px]"
      title={<span className="flex items-center gap-2">{M && <M.icon className="h-4 w-4 text-ember" />}{n?.name ?? nodeId}</span>}
      subtitle={n ? <span className="mono">{n.id} · {M?.label}{n.agentId ? ` · ${n.agentId}` : ""}</span> : undefined}>
      {q.error ? <div className="p-4"><ErrorState error={q.error} onRetry={q.reload} /></div> : !n ? <div className="space-y-3 p-4"><Skeleton className="h-20" /><Skeleton className="h-40" /></div> : (
        <div>
          <div className="flex items-center gap-2 border-b border-line px-4 py-2.5"><NodeStateBadge state={n.state} />{n.attempt > 1 && <Badge>attempt {n.attempt}</Badge>}{n.iteration > 0 && <Badge>loop {n.iteration}</Badge>}{n.reusedFromRun && <Badge tone="info">reused from replayed run</Badge>}</div>
          <Tabs className="px-2" value={tab} onChange={setTab} tabs={[
            { id: "overview", label: "Overview" }, { id: "io", label: "Input / output" }, { id: "model", label: "Model calls", count: n.modelCalls.length },
            { id: "tools", label: "Tool calls", count: n.toolCalls.length }, { id: "verify", label: "Verification", count: n.verification.length || null }, { id: "events", label: "Events", count: n.events.length }]} />
          <div className="p-4">
            {tab === "overview" && (
              <div className="space-y-4">
                {n.error && <Failure nodeName={n.name} error={n.error} />}
                {n.state === "BLOCKED" && n.blockedReason === "upstream_failed" && <p className="rounded border border-line bg-raised/50 p-3 text-sm text-muted">Not run: an upstream step failed, so this step never received valid input.</p>}
                <dl className="rounded border border-line bg-raised/40 px-3 py-1">
                  <Kv k="Started">{fmtDateTime(n.startedAt)}</Kv>
                  <Kv k="Finished">{fmtDateTime(n.finishedAt)}</Kv>
                  <Kv k="Duration">{n.startedAt ? fmtDuration((new Date(n.finishedAt ?? Date.now()).getTime() - new Date(n.startedAt).getTime()) / 1000) : "—"}</Kv>
                  <Kv k="Timeout">{n.definition?.timeoutS ?? "—"} s</Kv>
                  <Kv k="Checkpoints">{n.checkpoints.length}</Kv>
                </dl>
                {n.model && (
                  <div>
                    <p className="mb-1 text-xs font-medium text-muted">Model routing</p>
                    <div className="rounded border border-line bg-raised/40 p-3">
                      <p className="flex items-center gap-2"><span className="mono text-sm">{n.model}</span>{n.routing?.tier && <Badge tone="info">{n.routing.tier}</Badge>}</p>
                      {n.routing?.reasons && <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-muted">{n.routing.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
                      {n.routing?.fallbackModel && <p className="mt-2 text-xs text-faint">Fallback: <span className="mono">{n.routing.fallbackModel}</span></p>}
                    </div>
                  </div>
                )}
                {n.feedback && <div><p className="mb-1 text-xs font-medium text-muted">Feedback given on the last re-run</p><JsonTree data={n.feedback} maxHeight={180} /></div>}
                {n.definition?.tools?.length > 0 && <div><p className="mb-1 text-xs font-medium text-muted">Permitted tools</p><div className="flex flex-wrap gap-1.5">{n.definition.tools.map((t: string) => <span key={t} className="mono rounded border border-line-strong px-1.5 py-0.5 text-xs">{t}</span>)}</div></div>}
                {n.artifacts.length > 0 && <div><p className="mb-1 text-xs font-medium text-muted">Artifacts</p><div className="flex flex-wrap gap-1.5">{n.artifacts.map((a) => <button key={a.id} onClick={() => onArtifact(a.id)} className="rounded border border-line-strong px-1.5 py-0.5 text-xs text-info hover:bg-hover">{a.type}</button>)}</div></div>}
              </div>
            )}
            {tab === "io" && (
              <div className="space-y-4">
                <div><p className="mb-1 text-xs font-medium text-muted">Input (validated against the input schema)</p>{n.input ? <JsonTree data={n.input} maxHeight={260} /> : <p className="text-sm text-faint">Not available: the node has not received input yet.</p>}</div>
                <div><p className="mb-1 text-xs font-medium text-muted">Output (validated against the output schema)</p>{n.output ? <JsonTree data={n.output} maxHeight={320} /> : <p className="text-sm text-faint">Not available: the node has not produced output.</p>}</div>
              </div>
            )}
            {tab === "model" && (n.modelCalls.length === 0 ? <p className="text-sm text-faint">This node made no model calls.</p> : (
              <table className={tableCls.table}>
                <thead><tr><th className={tableCls.th}>Model</th><th className={cn(tableCls.th, "text-right")}>In → out</th><th className={cn(tableCls.th, "text-right")}>Latency</th><th className={cn(tableCls.th, "text-right")}>Cost</th></tr></thead>
                <tbody>{n.modelCalls.map((m) => (
                  <tr key={m.id} className={tableCls.tr}>
                    <td className={tableCls.td}><span className="mono text-xs">{m.model}</span>{m.purpose !== "node" && <Badge className="ml-1.5">{m.purpose}</Badge>}{m.status === "error" && <span className="ml-1.5 text-xs text-bad" title={m.errorMessage ?? ""}>{m.errorClass}</span>}</td>
                    <td className={cn(tableCls.td, "num text-right")}>{m.inputTokens ?? "—"} → {m.outputTokens ?? "—"}</td>
                    <td className={cn(tableCls.td, "num text-right")}>{fmtMs(m.latencyMs)}</td>
                    <td className={cn(tableCls.td, "text-right")}><Money value={m.costUsd} basis={m.costBasis} /></td>
                  </tr>))}</tbody>
              </table>
            ))}
            {tab === "tools" && (n.toolCalls.length === 0 ? <p className="text-sm text-faint">This node made no tool calls.</p> : (
              <ul className="space-y-2">
                {n.toolCalls.map((t) => (
                  <li key={t.id} className={cn("rounded border p-2.5", t.decision === "DENY" ? "border-warn/50 bg-warn/5" : "border-line")}>
                    <div className="flex items-center gap-2"><span className="mono text-sm font-medium">{t.tool}</span>
                      {t.decision === "DENY" ? <Badge tone="warn">blocked by policy</Badge> : <Badge tone={t.status === "ok" ? "ok" : "bad"}>{t.status}</Badge>}
                      <span className="ml-auto num text-xs text-faint">{fmtTime(t.ts)}{t.durationMs != null ? ` · ${fmtMs(t.durationMs)}` : ""}</span></div>
                    {t.decision === "DENY" && <p className="mt-1 text-xs text-warn">{t.operation} denied: {t.reason}. No changes were made.</p>}
                    {t.error && <p className="mt-1 text-xs text-bad">{t.error}</p>}
                    <details className="mt-1.5"><summary className="cursor-pointer text-xs text-faint hover:text-muted">Input{t.summary ? " & result summary" : ""}</summary><div className="mt-1.5 space-y-1.5"><JsonTree data={t.input} maxHeight={160} />{t.summary && <JsonTree data={t.summary} maxHeight={160} />}</div></details>
                  </li>
                ))}
              </ul>
            ))}
            {tab === "verify" && (n.verification.length === 0 ? <p className="text-sm text-faint">This node produced no verification results.</p> : (
              <ul className="space-y-2">{n.verification.map((v) => (
                <li key={v.findingId} className="rounded border border-line p-2.5">
                  <div className="flex items-center gap-2"><VerificationBadge status={v.status} /><span className="mono text-xs">{v.findingId}</span><span className="ml-auto num text-xs text-faint">conf {v.confidence.toFixed(2)} · evidence {v.evidenceScore.toFixed(2)}</span></div>
                  <p className="mt-1.5 text-sm text-muted">{v.reason}</p>
                  {v.missingEvidence.length > 0 && <p className="mt-1 text-xs text-warn">Missing: {v.missingEvidence.join("; ")}</p>}
                </li>))}</ul>
            ))}
            {tab === "events" && (
              <ol className="space-y-1">{n.events.map((e) => <li key={e.id} className="flex gap-2 text-xs"><span className="num shrink-0 text-faint">{fmtTime(e.ts)}</span><span className="text-muted">{describe(e)}</span></li>)}</ol>
            )}
          </div>
        </div>
      )}
    </Drawer>
  );
}
