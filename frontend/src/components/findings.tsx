"use client";
import * as React from "react";
import { Check, ChevronDown, ChevronRight, Minus, X } from "lucide-react";
import { SeverityBadge, VerificationBadge } from "@/components/status";
import { CodeBlock } from "@/components/viz";
import { Empty, tableCls } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import type { VerificationRow } from "@/lib/types";

const CHECKS: [string, string][] = [
  ["schema", "Valid SecurityFinding schema"], ["file_exists", "Cited file exists in the snapshot"], ["snippet_present", "Quoted evidence appears at that location"],
  ["rule_recheck", "Independent rule re-check agrees"], ["trace_inspected", "Agent's tool trace shows it inspected the file"],
  ["external_evidence", "External evidence supports it"], ["dependency_declared", "Dependency declared in a manifest"], ["osv_confirms", "OSV advisory confirms it"],
];

function CheckRow({ label, v }: { label: string; v: boolean | undefined }) {
  return (
    <li className="flex items-center gap-2 text-xs">
      {v === true ? <Check className="h-3.5 w-3.5 text-ok" /> : v === false ? <X className="h-3.5 w-3.5 text-bad" /> : <Minus className="h-3.5 w-3.5 text-faint" />}
      <span className={cn(v === undefined ? "text-faint" : "text-muted")}>{label}</span>
    </li>
  );
}

export function FindingsTable({ rows }: { rows: VerificationRow[] }) {
  const [open, setOpen] = React.useState<string | null>(null);
  const order = { VERIFIED: 0, UNCERTAIN: 1, REJECTED: 2 } as const;
  const sorted = [...rows].sort((a, b) => order[a.status] - order[b.status] || (a.finding?.priority ?? 99) - (b.finding?.priority ?? 99));
  const count = (s: string) => rows.filter((r) => r.status === s).length;
  if (rows.length === 0) return <Empty title="No findings have been verified yet">Findings appear here once the verification step has run. Each is labelled VERIFIED, UNCERTAIN or REJECTED, and never silently upgraded.</Empty>;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-4 border-b border-line px-4 py-2.5 text-sm">
        <span><b className="num text-ok">{count("VERIFIED")}</b> <span className="text-muted">verified</span></span>
        <span><b className="num text-warn">{count("UNCERTAIN")}</b> <span className="text-muted">uncertain (unverified)</span></span>
        <span><b className="num text-bad">{count("REJECTED")}</b> <span className="text-muted">rejected</span></span>
      </div>
      <table className={tableCls.table}>
        <thead><tr><th className={cn(tableCls.th, "w-6")} /><th className={tableCls.th}>Verdict</th><th className={tableCls.th}>Severity</th><th className={tableCls.th}>Finding</th><th className={tableCls.th}>Location</th><th className={cn(tableCls.th, "text-right")}>Confidence</th><th className={cn(tableCls.th, "text-right")}>Evidence</th></tr></thead>
        <tbody>
          {sorted.map((r) => {
            const f = r.finding;
            const isOpen = open === r.findingId;
            return (
              <React.Fragment key={r.findingId}>
                <tr className={cn(tableCls.tr, "cursor-pointer", r.status === "REJECTED" && "opacity-70")} onClick={() => setOpen(isOpen ? null : r.findingId)}>
                  <td className={tableCls.td}>{isOpen ? <ChevronDown className="h-3.5 w-3.5 text-faint" /> : <ChevronRight className="h-3.5 w-3.5 text-faint" />}</td>
                  <td className={tableCls.td}><VerificationBadge status={r.status} /></td>
                  <td className={tableCls.td}>{f ? <SeverityBadge severity={f.severity} /> : "—"}</td>
                  <td className={cn(tableCls.td, "max-w-[360px]")}><p className="truncate font-medium">{f?.title ?? r.findingId}</p><p className="mono text-2xs text-faint">{r.findingId}{f?.category ? ` · ${f.category}` : ""}</p></td>
                  <td className={cn(tableCls.td, "mono max-w-[200px] truncate text-xs text-muted")}>{f ? `${f.file}${f.line ? `:${f.line}` : ""}` : "—"}</td>
                  <td className={cn(tableCls.td, "num text-right")}>{r.confidence.toFixed(2)}</td>
                  <td className={cn(tableCls.td, "num text-right")}>{r.evidenceScore.toFixed(2)}</td>
                </tr>
                {isOpen && (
                  <tr><td colSpan={7} className="border-b border-line bg-raised/30 px-6 py-4">
                    <div className="grid gap-6 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
                      <div className="space-y-3">
                        <p className="text-sm font-medium">{r.reason}</p>
                        {f && <>
                          <p className="text-sm text-muted">{f.description}</p>
                          <div><p className="mb-1 text-xs text-faint">Evidence quoted by the agent</p><CodeBlock code={f.evidence} maxHeight={140} /></div>
                          <div><p className="mb-1 text-xs text-faint">Suggested remediation</p><p className="text-sm text-muted">{f.remediation}</p></div>
                          {f.references && f.references.length > 0 && <p className="mono text-xs text-faint">{f.references.join(" · ")}</p>}
                        </>}
                      </div>
                      <div className="space-y-3">
                        <div><p className="mb-1.5 text-xs font-medium text-muted">What the verifier checked</p>
                          <ul className="space-y-1">{CHECKS.map(([k, l]) => <CheckRow key={k} label={l} v={typeof r.checks[k] === "boolean" ? r.checks[k] : undefined} />)}
                            {r.checks.judge !== undefined && <CheckRow label={r.checks.judge === "unavailable" ? "Independent model opinion (unavailable)" : `Independent model ${r.checks.judge.supports ? "agrees" : "disagrees"} (${Number(r.checks.judge.confidence).toFixed(2)})`} v={r.checks.judge === "unavailable" ? undefined : r.checks.judge.supports} />}</ul></div>
                        {r.missingEvidence.length > 0 && <div><p className="mb-1 text-xs font-medium text-warn">Missing evidence</p><ul className="list-disc space-y-0.5 pl-4 text-xs text-muted">{r.missingEvidence.map((m, i) => <li key={i}>{m}</li>)}</ul></div>}
                        {r.recommendations.length > 0 && <div><p className="mb-1 text-xs font-medium text-muted">Recommendations</p><ul className="list-disc space-y-0.5 pl-4 text-xs text-muted">{r.recommendations.map((m, i) => <li key={i}>{m}</li>)}</ul></div>}
                      </div>
                    </div>
                  </td></tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
