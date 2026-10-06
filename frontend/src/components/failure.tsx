"use client";
import * as React from "react";
import { ChevronDown, ChevronRight, XOctagon } from "lucide-react";
import { JsonTree } from "@/components/viz";
import type { NodeError } from "@/lib/types";

const CAUSE: Record<string, string> = {
  transient: "A temporary infrastructure problem", rate_limited: "The model provider rate-limited the request", timeout: "The step timed out",
  permanent: "A permanent configuration or provider error", policy_violation: "A policy violation", model_failure: "The model did not produce a usable response",
  tool_failure: "A tool failed repeatedly", validation_failure: "The output did not match its typed contract", budget: "The budget was reached",
};
const RECOVERY: Record<string, string> = {
  transient: "Retries were exhausted. You can retry the run; successful steps are not re-executed.",
  rate_limited: "Retries were exhausted. Retry later, or lower provider concurrency in Settings.",
  timeout: "Retries were exhausted. Increase the node timeout in the workflow, or retry.",
  permanent: "This kind of error is never retried automatically. Check provider credentials and model ids in Settings.",
  policy_violation: "Not retried: the attempted action was blocked and nothing changed. Review the node's tools and permissions.",
  model_failure: "Retried with the configured fallback model where available. Try a different model for this node.",
  tool_failure: "Retried, then stopped. Check the tool's configuration (e.g. Tavily key, repository).",
  validation_failure: "Retried with feedback, then stopped. Loosen the node's output schema or use a stronger model.",
  budget: "Increase the budget or retry with a cheaper model.",
};

export function Failure({ nodeName, error, compact }: { nodeName: string; error: NodeError; compact?: boolean }) {
  const [open, setOpen] = React.useState(false);
  const cause = error.cause ? error.cause[0].toUpperCase() + error.cause.slice(1) : CAUSE[error.class] ?? "An error";
  return (
    <div className="rounded-md border border-bad/35 bg-bad/5 p-3.5">
      <p className="flex items-center gap-2 text-sm font-semibold text-bad"><XOctagon className="h-4 w-4" /> Execution failed</p>
      <p className="mt-1.5 text-sm"><b>{nodeName}</b> could not complete.</p>
      <dl className="mt-2 space-y-2 text-sm">
        <div><dt className="text-xs text-faint">Cause</dt><dd className="text-muted">{cause}{error.code && error.code !== error.class.toUpperCase() ? ` (${error.code})` : ""}</dd></div>
        {!compact && <div><dt className="text-xs text-faint">Recovery</dt><dd className="text-muted">{error.retry_decision ? `${error.retry_decision}. ` : ""}{RECOVERY[error.class] ?? "You can retry the run."}</dd></div>}
      </dl>
      <button className="mt-2.5 inline-flex items-center gap-1 text-xs text-faint hover:text-muted" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} View technical details
      </button>
      {open && <div className="mt-2"><JsonTree data={error} maxHeight={220} /></div>}
    </div>
  );
}
