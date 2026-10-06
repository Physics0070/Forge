"use client";
import * as React from "react";
import {
  Ban, CheckCircle2, CircleDashed, CircleDot, FileSearch, GitBranch, GitMerge, Hand, Layers, Loader2, PauseCircle, RefreshCw, ShieldCheck, ShieldAlert,
  SkipForward, Sparkles, Timer, Wand2, Wrench, XCircle, HelpCircle, Bot,
} from "lucide-react";
import { Badge, Dot, type Tone } from "@/components/ui/primitives";
import { Tip } from "@/components/ui/overlay";
import { cn } from "@/lib/utils";
import { NA, fmtUsd } from "@/lib/format";
import type { CostBasis, NodeState, NodeType, RunStatus, VStatus } from "@/lib/types";

export const RUN_META: Record<RunStatus, { tone: Tone; label: string; live?: boolean }> = {
  PENDING: { tone: "neutral", label: "Pending" },
  RUNNING: { tone: "ember", label: "Running", live: true },
  PAUSED: { tone: "warn", label: "Paused" },
  WAITING_APPROVAL: { tone: "warn", label: "Waiting for approval" },
  BLOCKED: { tone: "bad", label: "Blocked" },
  SUCCESS: { tone: "ok", label: "Succeeded" },
  FAILED: { tone: "bad", label: "Failed" },
  CANCELLED: { tone: "neutral", label: "Cancelled" },
};
export const NODE_META: Record<NodeState, { tone: Tone; label: string; live?: boolean }> = {
  PENDING: { tone: "neutral", label: "Pending" },
  READY: { tone: "info", label: "Ready" },
  RUNNING: { tone: "ember", label: "Running", live: true },
  WAITING_APPROVAL: { tone: "warn", label: "Needs approval" },
  SUCCESS: { tone: "ok", label: "Succeeded" },
  FAILED: { tone: "bad", label: "Failed" },
  RETRYING: { tone: "warn", label: "Retrying", live: true },
  BLOCKED: { tone: "bad", label: "Blocked" },
  CANCELLED: { tone: "neutral", label: "Cancelled" },
  VERIFICATION_FAILED: { tone: "bad", label: "Verification failed" },
  RECOVERING: { tone: "warn", label: "Recovering", live: true },
  SKIPPED: { tone: "neutral", label: "Skipped" },
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const m = RUN_META[status] ?? RUN_META.PENDING;
  return (
    <Badge tone={m.tone}>
      <Dot tone={m.tone} pulse={m.live} />
      {m.label}
    </Badge>
  );
}
export function NodeStateBadge({ state }: { state: NodeState }) {
  const m = NODE_META[state] ?? NODE_META.PENDING;
  return (
    <Badge tone={m.tone}>
      <Dot tone={m.tone} pulse={m.live} />
      {m.label}
    </Badge>
  );
}

export function VerificationBadge({ status }: { status: VStatus | "UNVERIFIED" }) {
  const map: Record<string, { tone: Tone; icon: React.ReactNode }> = {
    VERIFIED: { tone: "ok", icon: <ShieldCheck className="h-3 w-3" /> },
    REJECTED: { tone: "bad", icon: <ShieldAlert className="h-3 w-3" /> },
    UNCERTAIN: { tone: "warn", icon: <HelpCircle className="h-3 w-3" /> },
    UNVERIFIED: { tone: "neutral", icon: <CircleDashed className="h-3 w-3" /> },
  };
  const m = map[status];
  return <Badge tone={m.tone}>{m.icon}{status}</Badge>;
}

const SEV: Record<string, Tone> = { critical: "bad", high: "bad", medium: "warn", low: "info", info: "neutral" };
export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge tone={SEV[severity] ?? "neutral"}>{severity}</Badge>;
}

export const NODE_TYPE_META: Record<NodeType, { icon: React.ComponentType<{ className?: string }>; label: string; hint: string }> = {
  AGENT: { icon: Bot, label: "Agent", hint: "Runs an agent with a model and permitted tools" },
  PARALLEL: { icon: GitBranch, label: "Parallel", hint: "Fan-out: all outgoing branches start together" },
  JOIN: { icon: GitMerge, label: "Join", hint: "Waits for required branches and merges their outputs" },
  CONDITION: { icon: Layers, label: "Condition", hint: "Routes to the true/false branch by a predicate" },
  RETRY: { icon: RefreshCw, label: "Retry gate", hint: "Re-runs its target until a condition holds (bounded)" },
  APPROVAL: { icon: Hand, label: "Approval", hint: "Pauses until a human approves or rejects" },
  VERIFICATION: { icon: FileSearch, label: "Verification", hint: "Independently verifies claims against evidence" },
  RECOVERY: { icon: Wrench, label: "Recovery", hint: "Repairs a watched node's output if it fails" },
  TRANSFORM: { icon: Wand2, label: "Transform", hint: "Deterministic reshaping of data, no model call" },
};

export function NodeStateIcon({ state, className }: { state: NodeState; className?: string }) {
  const c = cn("h-3.5 w-3.5", className);
  switch (state) {
    case "SUCCESS": return <CheckCircle2 className={cn(c, "text-ok")} />;
    case "FAILED": case "VERIFICATION_FAILED": return <XCircle className={cn(c, "text-bad")} />;
    case "RUNNING": return <Loader2 className={cn(c, "animate-spin text-ember")} />;
    case "RETRYING": case "RECOVERING": return <RefreshCw className={cn(c, "animate-spin text-warn")} />;
    case "WAITING_APPROVAL": return <Hand className={cn(c, "text-warn")} />;
    case "BLOCKED": return <Ban className={cn(c, "text-bad")} />;
    case "SKIPPED": return <SkipForward className={cn(c, "text-faint")} />;
    case "CANCELLED": return <PauseCircle className={cn(c, "text-faint")} />;
    case "READY": return <CircleDot className={cn(c, "text-info")} />;
    default: return <CircleDashed className={cn(c, "text-faint")} />;
  }
}

/** Cost is shown with its provenance. Missing price => "Not available", never $0. */
export function Money({ value, basis, partial, className }: { value: number | null | undefined; basis?: CostBasis; partial?: boolean; className?: string }) {
  if (value === null || value === undefined || basis === "UNAVAILABLE") {
    return (
      <Tip content="No price is configured for the models used, so FORGE cannot compute cost. It never guesses. Add prices in Settings → Models.">
        <span className={cn("text-faint", className, "font-sans text-sm font-normal")}>{NA}</span>
      </Tip>
    );
  }
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span className="num">{fmtUsd(value)}</span>
      {basis && <CostBasisTag basis={basis} partial={partial} />}
    </span>
  );
}
export function CostBasisTag({ basis, partial }: { basis: CostBasis; partial?: boolean }) {
  const tip =
    basis === "ACTUAL" ? "Provider-reported token usage × configured price." :
    basis === "ESTIMATED" ? "Token usage was estimated (provider returned no usage)." : "No cost data.";
  return (
    <Tip content={partial ? `${tip} Some calls have no configured price and are excluded.` : tip}>
      <span><Badge tone={basis === "ACTUAL" ? "neutral" : "warn"} className="normal-case">{basis.toLowerCase()}{partial ? " · partial" : ""}</Badge></span>
    </Tip>
  );
}

export function Metric({ label, value, sub, tone, hint }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: Tone; hint?: string }) {
  const colour = tone === "ok" ? "text-ok" : tone === "bad" ? "text-bad" : tone === "warn" ? "text-warn" : "text-fg";
  return (
    <div className="min-w-0 px-4 py-3">
      <Tip content={hint}>
        <p className="truncate text-xs text-faint">{label}</p>
      </Tip>
      {value === NA ? (
        <p className="mt-1 text-sm leading-6 text-faint">{NA}</p>
      ) : (
        <p className={cn("num mt-1 truncate text-[20px] font-semibold leading-6", colour)}>{value}</p>
      )}
      {sub && <p className="mt-0.5 truncate text-xs text-faint">{sub}</p>}
    </div>
  );
}

export function NA_({ children }: { children?: React.ReactNode }) {
  return <span className="text-faint">{children ?? NA}</span>;
}

export const Sparks = Sparkles;
export const Clock = Timer;
