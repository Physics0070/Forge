"use client";
import * as React from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Money, Metric } from "@/components/status";
import { Empty, tableCls } from "@/components/ui/primitives";
import { fmtInt, fmtMs } from "@/lib/format";
import { useQuery } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { CostBasis, Run } from "@/lib/types";

interface Call { id: string; nodeId: string | null; purpose: string; model: string; status: string; errorClass: string | null; inputTokens: number | null; outputTokens: number | null; latencyMs: number | null; costUsd: number | null; costBasis: CostBasis; ts: string }
const AXIS = { fontSize: 11, fill: "rgb(104,103,110)" };

export function CostPanel({ run, version }: { run: Run; version: string }) {
  const calls = useQuery<{ modelCalls: Call[] }>(`/api/runs/${run.id}/model-calls?v=${version}`);
  const t = run.totals;
  const byNode = React.useMemo(() => {
    const m = new Map<string, { node: string; input: number; output: number }>();
    for (const c of calls.data?.modelCalls ?? []) {
      const k = c.nodeId ?? c.purpose;
      const r = m.get(k) ?? { node: k, input: 0, output: 0 };
      r.input += c.inputTokens ?? 0;
      r.output += c.outputTokens ?? 0;
      m.set(k, r);
    }
    return [...m.values()];
  }, [calls.data]);
  if (t.modelCalls === 0) return <Empty title="No model calls yet">Token, cost and latency figures appear as nodes execute. Nothing is estimated in advance.</Empty>;
  return (
    <div>
      <div className="grid grid-cols-2 divide-x divide-line border-b border-line md:grid-cols-6">
        <Metric label="Model calls" value={t.modelCalls} sub={t.failedModelCalls ? `${t.failedModelCalls} failed` : "all succeeded"} tone={t.failedModelCalls ? "warn" : undefined} />
        <Metric label="Input tokens" value={fmtInt(t.inputTokens)} />
        <Metric label="Output tokens" value={fmtInt(t.outputTokens)} />
        <Metric label="Total tokens" value={fmtInt(t.totalTokens)} sub="provider-reported" />
        <Metric label="Cost" value={<Money value={t.costUsd} basis={t.costBasis} partial={t.costPartial} className="text-[20px] font-semibold" />} sub={t.unpricedCalls ? `${t.unpricedCalls} call(s) unpriced` : undefined} />
        <Metric label="Latency p50 / p95" value={`${fmtMs(t.latencyMs.p50)} / ${fmtMs(t.latencyMs.p95)}`} sub={`avg ${fmtMs(t.latencyMs.avg)}`} />
      </div>
      <div className="grid gap-6 p-4 lg:grid-cols-2">
        <div>
          <p className="mb-2 text-xs font-medium text-muted">Tokens by node</p>
          <div className="h-[220px]">
            <ResponsiveContainer>
              <BarChart data={byNode} layout="vertical" margin={{ left: 20, right: 8 }}>
                <CartesianGrid horizontal={false} stroke="rgb(38,38,44)" />
                <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="node" width={110} tick={AXIS} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ background: "rgb(25,25,29)", border: "1px solid rgb(58,58,66)", borderRadius: 6, fontSize: 12 }} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
                <Bar dataKey="input" name="Input" stackId="a" fill="rgb(120,170,255)" />
                <Bar dataKey="output" name="Output" stackId="a" fill="rgb(255,106,43)" radius={[0, 2, 2, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div>
          <p className="mb-2 text-xs font-medium text-muted">By model</p>
          <table className={tableCls.table}>
            <thead><tr><th className={tableCls.th}>Model</th><th className={cn(tableCls.th, "text-right")}>Calls</th><th className={cn(tableCls.th, "text-right")}>Tokens</th><th className={cn(tableCls.th, "text-right")}>Cost</th></tr></thead>
            <tbody>{t.byModel.map((m) => (
              <tr key={m.model} className={tableCls.tr}>
                <td className={cn(tableCls.td, "mono text-xs")}>{m.model}</td><td className={cn(tableCls.td, "num text-right")}>{m.calls}{m.errors ? <span className="text-bad"> ({m.errors} err)</span> : ""}</td>
                <td className={cn(tableCls.td, "num text-right")}>{fmtInt(m.inputTokens + m.outputTokens)}</td>
                <td className={cn(tableCls.td, "text-right")}><Money value={m.costUsd} basis={m.costUsd === null ? "UNAVAILABLE" : t.costBasis} /></td>
              </tr>))}</tbody>
          </table>
        </div>
      </div>
      <div className="border-t border-line">
        <table className={tableCls.table}>
          <thead><tr><th className={tableCls.th}>Node</th><th className={tableCls.th}>Model</th><th className={tableCls.th}>Purpose</th><th className={cn(tableCls.th, "text-right")}>In → out</th><th className={cn(tableCls.th, "text-right")}>Latency</th><th className={cn(tableCls.th, "text-right")}>Cost</th></tr></thead>
          <tbody>{(calls.data?.modelCalls ?? []).map((c) => (
            <tr key={c.id} className={tableCls.tr}>
              <td className={cn(tableCls.td, "mono text-xs")}>{c.nodeId ?? "—"}</td><td className={cn(tableCls.td, "mono text-xs")}>{c.model}</td>
              <td className={cn(tableCls.td, c.status === "error" && "text-bad")}>{c.status === "error" ? `error: ${c.errorClass}` : c.purpose}</td>
              <td className={cn(tableCls.td, "num text-right")}>{c.inputTokens ?? "—"} → {c.outputTokens ?? "—"}</td><td className={cn(tableCls.td, "num text-right")}>{fmtMs(c.latencyMs)}</td>
              <td className={cn(tableCls.td, "text-right")}><Money value={c.costUsd} basis={c.costBasis} /></td>
            </tr>))}</tbody>
        </table>
      </div>
    </div>
  );
}
