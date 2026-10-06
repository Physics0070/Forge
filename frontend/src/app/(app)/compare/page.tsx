"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowDown, ArrowUp, Equal } from "lucide-react";
import { Money, NodeStateBadge, RunStatusBadge } from "@/components/status";
import { Empty, ErrorState, PageHeader, Panel, Select, Skeleton, tableCls } from "@/components/ui/primitives";
import { useQuery } from "@/lib/hooks";
import { NA, fmtDuration, fmtInt, fmtPct, shortId } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Metrics, Run } from "@/lib/types";

interface Cmp { a: Run; b: Run; metricsA: Metrics; metricsB: Metrics; diff: Record<string, { a: any; b: any; differs: boolean; delta: number | null }>; sameWorkflowVersion: boolean; sameInput: boolean }

// lower is better for these; for the rest higher is better
const LOWER = new Set(["durationS", "totalTokens", "costUsd", "retries", "failures", "policyViolations", "modelCalls"]);
const ROWS: [string, string, (v: any) => React.ReactNode][] = [
  ["success", "Success", (v) => (v === null ? NA : v ? "yes" : "no")],
  ["durationS", "Duration", (v) => fmtDuration(v)],
  ["totalTokens", "Tokens", (v) => fmtInt(v)],
  ["costUsd", "Cost", (v) => (v === null ? NA : `$${Number(v).toFixed(4)}`)],
  ["modelCalls", "Model calls", (v) => fmtInt(v)],
  ["retries", "Retries", (v) => fmtInt(v)],
  ["failures", "Node failures", (v) => fmtInt(v)],
  ["verificationRate", "Verification rate", (v) => fmtPct(v)],
  ["schemaValidity", "Schema validity", (v) => fmtPct(v)],
  ["policyViolations", "Policy violations", (v) => fmtInt(v)],
  ["artifacts", "Artifacts", (v) => fmtInt(v)],
];

function ComparePageInner() {
  const sp = useSearchParams();
  const router = useRouter();
  const a = sp.get("a") ?? "";
  const b = sp.get("b") ?? "";
  const runs = useQuery<{ runs: Run[] }>("/api/runs?limit=100");
  const cmp = useQuery<Cmp>(a && b && a !== b ? `/api/runs/compare?a=${a}&b=${b}` : null);
  const ra = useQuery<Run>(a ? `/api/runs/${a}` : null);
  const rb = useQuery<Run>(b ? `/api/runs/${b}` : null);
  const set = (k: "a" | "b", v: string) => {
    const p = new URLSearchParams(sp.toString());
    p.set(k, v);
    router.replace(`/compare?${p.toString()}`);
  };
  const opts = runs.data?.runs ?? [];
  const nodeIds = [...new Set([...(ra.data?.nodes ?? []).map((n) => n.id), ...(rb.data?.nodes ?? []).map((n) => n.id)])];

  return (
    <div className="mx-auto max-w-[1100px] p-6">
      <PageHeader title="Compare runs" subtitle="Side by side, from recorded data only. Differences are highlighted; better values are marked." />
      <div className="mb-4 grid gap-3 md:grid-cols-2">
        {(["a", "b"] as const).map((k) => (
          <Select key={k} aria-label={`Run ${k.toUpperCase()}`} value={k === "a" ? a : b} onChange={(e) => set(k, e.target.value)}>
            <option value="">Select run {k.toUpperCase()}…</option>
            {opts.map((r) => <option key={r.id} value={r.id}>{shortId(r.id)} · {r.workflowName} v{r.version} · {r.status.toLowerCase()}{r.replayConfig?.model ? ` · ${r.replayConfig.model}` : ""}</option>)}
          </Select>
        ))}
      </div>
      {!a || !b || a === b ? (
        <Panel><Empty title="Pick two different runs">Tip: select two runs on the Runs page and press “Compare selected”.</Empty></Panel>
      ) : cmp.error ? <ErrorState error={cmp.error} /> : !cmp.data ? <Skeleton className="h-64" /> : (
        <div className="space-y-4">
          <Panel flush>
            <div className="grid grid-cols-2 divide-x divide-line">
              {[cmp.data.a, cmp.data.b].map((r, i) => (
                <div key={r.id} className="p-4">
                  <p className="text-xs text-faint">Run {i ? "B" : "A"}</p>
                  <Link href={`/runs/${r.id}`} className="mt-0.5 block font-medium hover:text-ember">{r.workflowName} <span className="mono text-xs text-muted">v{r.version} · {shortId(r.id)}</span></Link>
                  <div className="mt-1.5 flex items-center gap-2"><RunStatusBadge status={r.status} />{r.replayConfig?.model && <span className="mono text-xs text-muted">{r.replayConfig.model}</span>}</div>
                  <p className="mt-2 text-xs text-faint">Models: <span className="mono">{r.totals.byModel.map((m) => m.model).join(", ") || "none"}</span></p>
                </div>
              ))}
            </div>
            <p className="border-t border-line px-4 py-2 text-xs text-faint">
              {cmp.data.sameWorkflowVersion ? "Same workflow version" : "Different workflow versions"} · {cmp.data.sameInput ? "same input" : "different input"}
            </p>
          </Panel>
          <Panel title="Metrics" flush>
            <table className={tableCls.table}>
              <thead><tr><th className={tableCls.th}>Metric</th><th className={cn(tableCls.th, "text-right")}>Run A</th><th className={cn(tableCls.th, "text-right")}>Run B</th><th className={cn(tableCls.th, "w-28 text-right")}>Change</th></tr></thead>
              <tbody>
                {ROWS.map(([k, label, fmt]) => {
                  const d = cmp.data!.diff[k] ?? { a: (cmp.data!.metricsA as any)[k], b: (cmp.data!.metricsB as any)[k], differs: false, delta: null };
                  const better = d.delta === null || d.delta === 0 ? null : LOWER.has(k) ? d.delta < 0 : d.delta > 0;
                  return (
                    <tr key={k} className={cn(tableCls.tr, d.differs && "bg-ember/[0.03]")}>
                      <td className={tableCls.td}>{label}</td>
                      <td className={cn(tableCls.td, "num text-right")}>{k === "costUsd" ? <Money value={d.a} basis={cmp.data!.metricsA.costBasis} /> : fmt(d.a)}</td>
                      <td className={cn(tableCls.td, "num text-right", d.differs && "font-medium")}>{k === "costUsd" ? <Money value={d.b} basis={cmp.data!.metricsB.costBasis} /> : fmt(d.b)}</td>
                      <td className={cn(tableCls.td, "num text-right text-xs")}>
                        {!d.differs ? <Equal className="ml-auto h-3.5 w-3.5 text-faint" /> : d.delta === null ? <span className="text-warn">changed</span> : (
                          <span className={cn("inline-flex items-center gap-0.5", better === null ? "text-muted" : better ? "text-ok" : "text-bad")}>
                            {d.delta > 0 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />}{Math.abs(d.delta) < 1 ? Math.abs(d.delta).toFixed(3) : fmtInt(Math.round(Math.abs(d.delta)))}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Panel>
          <Panel title="Step by step" flush>
            <table className={tableCls.table}>
              <thead><tr><th className={tableCls.th}>Step</th><th className={tableCls.th}>Run A</th><th className={tableCls.th}>Run B</th><th className={tableCls.th}>Model A → B</th></tr></thead>
              <tbody>{nodeIds.map((nid) => {
                const x = ra.data?.nodes?.find((n) => n.id === nid), y = rb.data?.nodes?.find((n) => n.id === nid);
                return (
                  <tr key={nid} className={cn(tableCls.tr, x?.state !== y?.state && "bg-ember/[0.03]")}>
                    <td className={cn(tableCls.td, "mono text-xs")}>{nid}</td>
                    <td className={tableCls.td}>{x ? <NodeStateBadge state={x.state} /> : <span className="text-faint">absent</span>}</td>
                    <td className={tableCls.td}>{y ? <NodeStateBadge state={y.state} /> : <span className="text-faint">absent</span>}</td>
                    <td className={cn(tableCls.td, "mono text-xs text-muted")}>{x?.model ?? "—"} → {y?.model ?? "—"}{y?.reusedFromRun ? " (reused)" : ""}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </Panel>
        </div>
      )}
    </div>
  );
}

export default function ComparePage() {
  return (
    <React.Suspense fallback={null}>
      <ComparePageInner />
    </React.Suspense>
  );
}
