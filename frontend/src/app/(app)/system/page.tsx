"use client";
import * as React from "react";
import { Activity, CheckCircle2, CircleSlash, Cpu, Database, Radio, XCircle } from "lucide-react";
import { Metric } from "@/components/status";
import { Badge, Button, ErrorState, Kv, PageHeader, Panel, Skeleton, tableCls } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/overlay";
import { api } from "@/lib/api";
import { useQuery } from "@/lib/hooks";
import { NA, fmtDateTime, fmtDuration, fmtMs, fmtPct } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Health {
  database: { ok: boolean };
  workers: { id: string; startedAt: string; lastHeartbeatS: number; healthy: boolean }[];
  queue: { queued: number; leased: number; oldestQueuedAgeS: number | null };
  provider: { name: string; configured: boolean; last1h: { calls: number; errors: number; errorRate: number | null; latencyP50Ms: number | null; latencyP95Ms: number | null; errorsByClass: Record<string, number> }; live?: { ok: boolean; latency_ms: number; model_count?: number; kind?: string; message?: string } };
  failedNodes24h: number; runs24h: Record<string, number>; avgRunDuration24hS: number | null;
  sandbox: { enabled: boolean; dockerAvailable: boolean }; tavily: { configured: boolean }; storage: { driver: string };
}

function Ok({ ok, label }: { ok: boolean; label: string }) {
  return <span className={cn("inline-flex items-center gap-1.5 text-sm", ok ? "text-ok" : "text-bad")}>{ok ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}{label}</span>;
}

export default function SystemPage() {
  const q = useQuery<Health>("/api/system/health", { every: 5000 });
  const [live, setLive] = React.useState<Health["provider"]["live"] | null>(null);
  const [checking, setChecking] = React.useState(false);
  const { push } = useToast();
  async function check() {
    setChecking(true);
    try { const h = await api.get<Health>("/api/system/health?live=true"); setLive(h.provider.live ?? null); } catch (e) { push({ tone: "bad", title: "Live check failed", body: String((e as Error).message) }); } finally { setChecking(false); }
  }
  if (q.error) return <div className="p-6"><ErrorState error={q.error} onRetry={q.reload} /></div>;
  const h = q.data;
  const healthyWorkers = h?.workers.filter((w) => w.healthy).length ?? 0;
  return (
    <div className="mx-auto max-w-[1180px] p-6">
      <PageHeader title="System health" subtitle="The orchestrator observing itself: workers, durable queue, model provider, failures. Refreshes every 5 seconds." />
      {!h ? <Skeleton className="h-64" /> : (
        <div className="space-y-4">
          <Panel flush>
            <div className="grid grid-cols-2 divide-x divide-line md:grid-cols-5">
              <Metric label="Healthy workers" value={`${healthyWorkers}/${h.workers.length}`} tone={healthyWorkers ? "ok" : "bad"} sub={healthyWorkers ? "heartbeating" : "no worker is running"} />
              <Metric label="Queue depth" value={h.queue.queued} sub={`${h.queue.leased} executing`} tone={h.queue.queued > 20 ? "warn" : undefined} />
              <Metric label="Oldest queued" value={h.queue.oldestQueuedAgeS === null ? "—" : fmtDuration(h.queue.oldestQueuedAgeS)} tone={(h.queue.oldestQueuedAgeS ?? 0) > 60 ? "warn" : undefined} />
              <Metric label="Failed nodes (24h)" value={h.failedNodes24h} tone={h.failedNodes24h ? "warn" : "ok"} />
              <Metric label="Avg run duration (24h)" value={h.avgRunDuration24hS === null ? NA : fmtDuration(h.avgRunDuration24hS)} />
            </div>
          </Panel>
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Model provider" subtitle="Nebius Token Factory · last hour, this workspace" actions={<Button size="xs" loading={checking} onClick={check}><Radio className="h-3 w-3" /> Live check</Button>}>
              <dl>
                <Kv k="API key"><Ok ok={h.provider.configured} label={h.provider.configured ? "configured" : "missing: set NEBIUS_API_KEY"} /></Kv>
                <Kv k="Calls">{h.provider.last1h.calls}</Kv>
                <Kv k="Error rate">{fmtPct(h.provider.last1h.errorRate, 1)}</Kv>
                <Kv k="Latency p50 / p95">{fmtMs(h.provider.last1h.latencyP50Ms)} / {fmtMs(h.provider.last1h.latencyP95Ms)}</Kv>
                <Kv k="Errors by class">{Object.keys(h.provider.last1h.errorsByClass).length ? Object.entries(h.provider.last1h.errorsByClass).map(([k, v]) => `${k} ${v}`).join(" · ") : "none"}</Kv>
              </dl>
              {live && (
                <div className={cn("mt-3 rounded border p-2.5 text-sm", live.ok ? "border-ok/40 bg-ok/5" : "border-bad/40 bg-bad/5")}>
                  {live.ok ? <>Reachable in {fmtMs(live.latency_ms)} · {live.model_count} models available</> : <>Unreachable: <span className="mono">{live.kind}</span>, {live.message}</>}
                </div>
              )}
            </Panel>
            <Panel title="Dependencies">
              <dl>
                <Kv k={"Database"}><Ok ok={h.database.ok} label="PostgreSQL" /></Kv>
                <Kv k="Object storage"><span className="inline-flex items-center gap-1.5"><Database className="h-4 w-4 text-faint" />{h.storage.driver === "s3" ? "S3-compatible" : "local filesystem (single host)"}</span></Kv>
                <Kv k="Test sandbox">{h.sandbox.enabled && h.sandbox.dockerAvailable ? <Ok ok label="Docker available" /> : <span className="inline-flex items-center gap-1.5 text-warn"><CircleSlash className="h-4 w-4" />{h.sandbox.enabled ? "Docker not reachable: tests report NOT_AVAILABLE" : "disabled"}</span>}</Kv>
                <Kv k="Web research (Tavily)">{h.tavily.configured ? <Ok ok label="configured" /> : <span className="text-warn">not configured: research degrades gracefully</span>}</Kv>
                <Kv k="Runs (24h)">{Object.keys(h.runs24h).length ? Object.entries(h.runs24h).map(([k, v]) => `${k.toLowerCase()} ${v}`).join(" · ") : "none"}</Kv>
              </dl>
            </Panel>
          </div>
          <Panel title="Workers" subtitle="Each worker heartbeats and renews its job leases; an expired lease is requeued and resumed from its checkpoint." flush>
            {h.workers.length === 0 ? <p className="flex items-center gap-2 px-4 py-6 text-sm text-warn"><Cpu className="h-4 w-4" /> No worker has reported in. Start one with <span className="mono">python -m forge.engine.worker</span>.</p> : (
              <table className={tableCls.table}>
                <thead><tr><th className={tableCls.th}>Worker</th><th className={tableCls.th}>Status</th><th className={tableCls.th}>Started</th><th className={cn(tableCls.th, "text-right")}>Last heartbeat</th></tr></thead>
                <tbody>{h.workers.map((w) => (
                  <tr key={w.id} className={tableCls.tr}><td className={cn(tableCls.td, "mono text-xs")}>{w.id}</td><td className={tableCls.td}>{w.healthy ? <Badge tone="ok"><Activity className="h-3 w-3" /> healthy</Badge> : <Badge tone="bad">stale</Badge>}</td>
                    <td className={cn(tableCls.td, "text-muted")}>{fmtDateTime(w.startedAt)}</td><td className={cn(tableCls.td, "num text-right")}>{w.lastHeartbeatS.toFixed(0)} s ago</td></tr>))}</tbody>
              </table>
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}
