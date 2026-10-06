"use client";
import Link from "next/link";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, Area, AreaChart } from "recharts";
import { ArrowRight, Hand, Rocket } from "lucide-react";
import { Metric, Money } from "@/components/status";
import { RunsTable } from "@/components/runs-table";
import { Button, Empty, ErrorState, PageHeader, Panel, Skeleton } from "@/components/ui/primitives";
import { useQuery } from "@/lib/hooks";
import { NA, fmtCompact, fmtDuration, fmtPct } from "@/lib/format";
import type { Approval, Run } from "@/lib/types";

interface Dash {
  empty: boolean; activeRuns: number; completedRuns: number; totalRuns: number; successRate: number | null; verificationRate: number | null;
  verifiedFindings: number; totalTokens: number; totalCostUsd: number | null; costBasis: "ACTUAL" | "ESTIMATED" | "UNAVAILABLE"; costPartial: boolean;
  avgRunDurationS: number | null; policyViolations: number; pendingApprovals: number;
  series: { day: string; runs: number; success: number; failed: number; tokens: number }[];
}

const AXIS = { fontSize: 11, fill: "rgb(104,103,110)" };
const TT = { background: "rgb(25,25,29)", border: "1px solid rgb(58,58,66)", borderRadius: 6, fontSize: 12, color: "rgb(236,235,232)" };

export default function DashboardPage() {
  const d = useQuery<Dash>("/api/dashboard", { every: 10000 });
  const runs = useQuery<{ runs: Run[] }>("/api/runs?limit=8", { every: 10000 });
  const appr = useQuery<{ approvals: Approval[] }>("/api/approvals", { every: 10000 });

  if (d.error) return <div className="p-6"><ErrorState error={d.error} onRetry={d.reload} title="Couldn't load the dashboard" /></div>;
  const m = d.data;

  return (
    <div className="mx-auto max-w-[1280px] p-6">
      <PageHeader title="Dashboard" subtitle="Everything below is computed from real runs in this workspace. Where there's no data, it says so." actions={<Link href="/projects"><Button variant="primary">New workflow</Button></Link>} />

      {!m ? (
        <Skeleton className="h-24 w-full" />
      ) : m.empty ? (
        <Panel>
          <Empty icon={<Rocket className="h-6 w-6" />} title="No execution data yet." action={<Link href="/projects"><Button variant="primary">Create your first workflow <ArrowRight className="h-3.5 w-3.5" /></Button></Link>}>
            Create a project, attach a repository, and let FORGE compile an objective into a verifiable workflow.
          </Empty>
        </Panel>
      ) : (
        <>
          <Panel flush className="mb-4 overflow-hidden">
            <div className="grid grid-cols-2 divide-x divide-y divide-line md:grid-cols-4 md:divide-y-0 [&>*:nth-child(n+5)]:md:border-t [&>*:nth-child(n+5)]:md:border-line">
              <Metric label="Active runs" value={m.activeRuns} sub={m.pendingApprovals ? `${m.pendingApprovals} awaiting approval` : "none waiting"} />
              <Metric label="Completed runs" value={m.completedRuns} sub={`${m.totalRuns} total`} />
              <Metric label="Success rate" value={fmtPct(m.successRate)} tone={m.successRate === null ? undefined : m.successRate >= 0.8 ? "ok" : "warn"} sub={m.successRate === null ? "no finished runs" : "of finished runs"} />
              <Metric label="Verification rate" value={fmtPct(m.verificationRate)} sub={m.verificationRate === null ? "nothing verified yet" : `${m.verifiedFindings} verified findings`} hint="Findings independently verified ÷ findings checked" />
              <Metric label="Total tokens" value={fmtCompact(m.totalTokens)} sub="provider-reported" />
              <Metric label="Total cost" value={<Money value={m.totalCostUsd} basis={m.costBasis} partial={m.costPartial} className="text-[20px] font-semibold" />} sub={m.costBasis === "UNAVAILABLE" ? "no model price configured" : "configured price × usage"} />
              <Metric label="Avg run duration" value={m.avgRunDurationS === null ? NA : fmtDuration(m.avgRunDurationS)} sub="finished runs" />
              <Metric label="Policy violations" value={m.policyViolations} tone={m.policyViolations ? "warn" : "ok"} sub={m.policyViolations ? "blocked before any effect" : "none"} />
            </div>
          </Panel>

          <div className="mb-4 grid gap-4 lg:grid-cols-2">
            <Panel title="Runs per day" subtitle="Last 14 days">
              <div className="h-[200px]">
                <ResponsiveContainer>
                  <BarChart data={m.series} margin={{ left: -20, right: 4, top: 4 }}>
                    <CartesianGrid vertical={false} stroke="rgb(38,38,44)" />
                    <XAxis dataKey="day" tickFormatter={(v: string) => v.slice(5)} tick={AXIS} axisLine={false} tickLine={false} />
                    <YAxis allowDecimals={false} tick={AXIS} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={TT} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
                    <Bar dataKey="success" name="Succeeded" stackId="a" fill="rgb(62,207,142)" radius={[0, 0, 0, 0]} />
                    <Bar dataKey="failed" name="Failed" stackId="a" fill="rgb(240,98,98)" radius={[2, 2, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Panel>
            <Panel title="Tokens per day" subtitle="Provider-reported usage">
              <div className="h-[200px]">
                <ResponsiveContainer>
                  <AreaChart data={m.series} margin={{ left: -10, right: 4, top: 4 }}>
                    <CartesianGrid vertical={false} stroke="rgb(38,38,44)" />
                    <XAxis dataKey="day" tickFormatter={(v: string) => v.slice(5)} tick={AXIS} axisLine={false} tickLine={false} />
                    <YAxis tickFormatter={(v: number) => fmtCompact(v)} tick={AXIS} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={TT} />
                    <Area dataKey="tokens" name="Tokens" stroke="rgb(255,106,43)" fill="rgba(255,106,43,0.14)" strokeWidth={1.5} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </Panel>
          </div>

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
            <Panel title="Recent runs" flush actions={<Link href="/runs" className="text-xs text-muted hover:text-fg">View all</Link>}>
              {runs.data ? <RunsTable runs={runs.data.runs} compact /> : <Skeleton className="m-3 h-32" />}
            </Panel>
            <Panel title="Waiting on you" subtitle="Human approval gates" flush>
              {appr.data && appr.data.approvals.length > 0 ? (
                <ul className="divide-y divide-line">
                  {appr.data.approvals.map((a) => (
                    <li key={a.id}>
                      <Link href={`/runs/${a.runId}`} className="flex items-start gap-2.5 px-3.5 py-3 hover:bg-hover/60">
                        <Hand className="mt-0.5 h-4 w-4 shrink-0 text-warn" />
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-medium">{a.request.title ?? a.kind}</span>
                          <span className="block truncate text-xs text-faint">{a.request.summary || a.kind}</span>
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="px-3.5 py-6 text-center text-sm text-faint">Nothing is waiting for approval.</p>
              )}
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}
