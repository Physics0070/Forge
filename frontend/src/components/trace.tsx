"use client";
import * as React from "react";
import { AlertOctagon, ArrowRightLeft, Bot, CheckCircle2, CircleDot, FileBox, Hand, Network, Play, ShieldCheck, ShieldX, TriangleAlert, Wrench, XCircle, RefreshCw, Pause } from "lucide-react";
import { fmtMs, fmtUsd } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ForgeEvent } from "@/lib/types";
import { Select } from "@/components/ui/primitives";

type Group = "all" | "models" | "tools" | "state" | "policy" | "artifacts";
const GROUP_OF = (t: string): Exclude<Group, "all"> =>
  t === "MODEL_CALLED" ? "models" : t === "TOOL_CALLED" ? "tools" : t === "POLICY_BLOCKED" || t === "BUDGET_BLOCKED" ? "policy" : t === "ARTIFACT_CREATED" ? "artifacts" : "state";

function icon(e: ForgeEvent) {
  const c = "h-3.5 w-3.5 shrink-0";
  switch (e.type) {
    case "MODEL_CALLED": return <Bot className={cn(c, e.status === "error" ? "text-bad" : "text-info")} />;
    case "TOOL_CALLED": return <Wrench className={cn(c, e.status === "ok" ? "text-muted" : "text-bad")} />;
    case "POLICY_BLOCKED": case "BUDGET_BLOCKED": return <AlertOctagon className={cn(c, "text-warn")} />;
    case "ARTIFACT_CREATED": return <FileBox className={cn(c, "text-muted")} />;
    case "HANDOFF_VALIDATED": return <ArrowRightLeft className={cn(c, "text-ok")} />;
    case "HANDOFF_FAILED": case "HANDOFF_RETRY": return <ArrowRightLeft className={cn(c, "text-bad")} />;
    case "NODE_SUCCEEDED": case "NODE_RECOVERED": return <CheckCircle2 className={cn(c, "text-ok")} />;
    case "NODE_FAILED": case "VERIFICATION_FAILED": case "RUN_CANCELLED": return <XCircle className={cn(c, "text-bad")} />;
    case "NODE_STARTED": case "RUN_CREATED": return <Play className={cn(c, "text-ember")} />;
    case "RETRY_STARTED": case "RETRY_LOOPBACK": case "NODE_LOOPBACK": case "NODE_REQUEUED": case "WORKER_LOST": return <RefreshCw className={cn(c, "text-warn")} />;
    case "APPROVAL_REQUESTED": return <Hand className={cn(c, "text-warn")} />;
    case "APPROVAL_GRANTED": case "VERIFICATION_PASSED": return <ShieldCheck className={cn(c, "text-ok")} />;
    case "APPROVAL_REJECTED": return <ShieldX className={cn(c, "text-bad")} />;
    case "RUN_PAUSED": return <Pause className={cn(c, "text-warn")} />;
    case "WORKFLOW_COMPLETED": return <Network className={cn(c, e.status === "SUCCESS" ? "text-ok" : "text-bad")} />;
    default: return <CircleDot className={cn(c, "text-faint")} />;
  }
}

export function describe(e: ForgeEvent): string {
  const m = e.metadata ?? {};
  switch (e.type) {
    case "RUN_CREATED": return m.replay_of ? `Run created (replay of ${String(m.replay_of).slice(0, 8)})` : "Run created from approved workflow version";
    case "NODE_READY": return "Ready: inputs validated";
    case "NODE_STARTED": return `Started${m.worker ? ` on ${String(m.worker).split("-").slice(-1)[0]}` : ""} (attempt ${m.attempt ?? 1})`;
    case "MODEL_CALLED": return e.status === "error"
      ? `Model call failed: ${m.error_class ?? "error"} · ${m.model}` : `${m.purpose === "verify" ? "Verifier" : m.purpose === "compile" ? "Compiler" : "Model"} ${m.model} · ${m.input_tokens ?? "?"}→${m.output_tokens ?? "?"} tokens · ${fmtMs(m.latency_ms)}${m.cost_usd != null ? ` · ${fmtUsd(m.cost_usd)} (${String(m.cost_basis).toLowerCase()})` : ""}`;
    case "TOOL_CALLED": return `${m.tool} ${e.status === "ok" ? "ok" : "failed"}${m.duration_ms != null ? ` · ${fmtMs(m.duration_ms)}` : ""}${m.error ? ` · ${m.error}` : ""}`;
    case "HANDOFF_VALIDATED": return `Typed handoff validated from ${(m.sources ?? []).join(", ")}`;
    case "HANDOFF_FAILED": return `HANDOFF_FAILED: ${(m.errors ?? []).slice(0, 2).join("; ")}`;
    case "ARTIFACT_CREATED": return `Artifact ${m.artifact_type ?? ""} stored${m.hash ? ` · ${String(m.hash).slice(0, 10)}` : ""}${m.url ? ` · ${m.url}` : ""}`;
    case "NODE_SUCCEEDED": return "Succeeded";
    case "NODE_FAILED": return "Failed";
    case "NODE_SKIPPED": return `Skipped${m.reason ? `: ${m.reason}` : ""}`;
    case "NODE_BLOCKED": return `Blocked${m.reason ? `: ${m.reason}` : ""}`;
    case "RETRY_STARTED": return `Retrying in ${m.delay_s ?? 0}s: ${m.reason ?? ""}${m.fallback ? " (fallback model)" : ""}`;
    case "RETRY_LOOPBACK": return `Retry gate: condition not met, re-running ${m.target} (iteration ${m.iteration})`;
    case "NODE_RECOVERED": return `Output repaired by recovery node ${m.recovered_by}`;
    case "WORKER_LOST": return "Worker lease expired: node requeued, resuming from checkpoint";
    case "VERIFICATION_STARTED": return `Independent verification of ${m.count} finding(s)`;
    case "VERIFICATION_PASSED": return `Verified ${m.verified} · rejected ${m.rejected} · uncertain ${m.uncertain}`;
    case "VERIFICATION_FAILED": return m.verified === 0 ? "No finding could be verified" : "Verification failed";
    case "APPROVAL_REQUESTED": return `Approval requested: ${m.kind}`;
    case "APPROVAL_GRANTED": return "Approval granted by a human";
    case "APPROVAL_REJECTED": return "Approval rejected by a human";
    case "BUDGET_BLOCKED": return `Budget block: ${m.reason}`;
    case "POLICY_BLOCKED": return String(m.message ?? "Action blocked by policy");
    case "RUN_STATUS": return `Run is ${String(e.status).replace("_", " ").toLowerCase()}`;
    case "WORKFLOW_COMPLETED": return `Workflow ${String(e.status).toLowerCase()}${m.duration_s != null ? ` in ${m.duration_s}s` : ""}`;
    default: return e.type.toLowerCase().replace(/_/g, " ");
  }
}

function PolicyBanner({ e }: { e: ForgeEvent }) {
  const m = e.metadata;
  if (e.type === "BUDGET_BLOCKED") {
    return (
      <div className="my-1 rounded-md border border-warn/50 bg-warn/5 p-3 text-sm">
        <p className="flex items-center gap-1.5 font-semibold text-warn"><TriangleAlert className="h-4 w-4" /> EXECUTION BLOCKED · BUDGET</p>
        <p className="mt-1 text-muted">{m.reason}</p>
        <p className="mt-1 text-xs text-faint">Nothing was spent on the blocked call. Options: {(m.options ?? []).join(" · ").replace(/_/g, " ")}.</p>
      </div>
    );
  }
  return (
    <div className="my-1 rounded-md border border-warn/50 bg-warn/5 p-3 text-sm" role="alert">
      <p className="flex items-center gap-1.5 font-semibold text-warn"><TriangleAlert className="h-4 w-4" /> ACTION BLOCKED</p>
      <div className="mt-1.5 space-y-0.5">
        <p><span className="text-muted">{m.agent ?? e.nodeId}</span> attempted:</p>
        <p className="mono text-sm text-fg">{m.attempted}</p>
        <p className="pt-1"><span className="text-muted">Policy:</span> <span className="mono">{m.policy}</span></p>
        <p className="pt-1 font-medium text-ok">No changes were made.</p>
      </div>
    </div>
  );
}

export function TraceFeed({ events, startedAt, onNode, height = 320 }: { events: ForgeEvent[]; startedAt: string | null; onNode: (id: string) => void; height?: number }) {
  const [group, setGroup] = React.useState<Group>("all");
  const [node, setNode] = React.useState("");
  const [follow, setFollow] = React.useState(true);
  const box = React.useRef<HTMLDivElement>(null);
  const nodes = React.useMemo(() => [...new Set(events.map((e) => e.nodeId).filter(Boolean))] as string[], [events]);
  const shown = events.filter((e) => (group === "all" || GROUP_OF(e.type) === group) && (!node || e.nodeId === node));
  const t0 = startedAt ? new Date(startedAt).getTime() : events[0] ? new Date(events[0].ts).getTime() : 0;

  React.useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [shown.length, follow]);

  const groups: [Group, string][] = [["all", "All"], ["state", "State"], ["models", "Models"], ["tools", "Tools"], ["policy", "Policy"], ["artifacts", "Artifacts"]];
  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-2">
        {groups.map(([g, l]) => (
          <button key={g} onClick={() => setGroup(g)} className={cn("h-6 rounded px-2 text-xs transition-colors", group === g ? "bg-hover text-fg" : "text-faint hover:text-muted")}>{l}</button>
        ))}
        <div className="ml-auto flex items-center gap-2">
          <Select aria-label="Filter by node" value={node} onChange={(e) => setNode(e.target.value)} className="h-6 w-36 text-xs"><option value="">All nodes</option>{nodes.map((n) => <option key={n}>{n}</option>)}</Select>
          <label className="flex items-center gap-1 text-xs text-faint"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} className="accent-[rgb(var(--ember))]" /> follow</label>
        </div>
      </div>
      <div ref={box} className="overflow-y-auto" style={{ height }} role="log" aria-live="off">
        {shown.length === 0 && <p className="px-4 py-8 text-center text-sm text-faint">No events match.</p>}
        {shown.map((e) => (
          <div key={e.id} className={cn("group flex gap-2.5 border-b border-line/50 px-3 py-1.5 text-xs", e.type === "POLICY_BLOCKED" || e.type === "BUDGET_BLOCKED" ? "bg-warn/[0.03]" : "hover:bg-hover/40")}>
            <span className="num w-[52px] shrink-0 pt-px text-right text-faint">+{((new Date(e.ts).getTime() - t0) / 1000).toFixed(1)}s</span>
            <span className="pt-px">{icon(e)}</span>
            <div className="min-w-0 flex-1">
              {e.nodeId && <button className="mono mr-2 rounded bg-raised px-1 text-2xs text-muted hover:text-ember" onClick={() => onNode(e.nodeId!)}>{e.nodeId}</button>}
              {e.type === "POLICY_BLOCKED" || e.type === "BUDGET_BLOCKED" ? <PolicyBanner e={e} /> : (
                <span className={cn(e.type === "NODE_FAILED" || e.type === "HANDOFF_FAILED" ? "text-bad" : e.type === "WORKFLOW_COMPLETED" ? "font-medium text-fg" : "text-muted")}>{describe(e)}</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
