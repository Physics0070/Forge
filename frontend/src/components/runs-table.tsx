"use client";
import Link from "next/link";
import { GitCompare, RotateCcw } from "lucide-react";
import { Money, RunStatusBadge } from "@/components/status";
import { tableCls } from "@/components/ui/primitives";
import { fmtDuration, fmtInt, fmtRelative, shortId } from "@/lib/format";
import type { Run } from "@/lib/types";
import { cn } from "@/lib/utils";

export function RunsTable({ runs, compact, selectable, selected, onSelect }: {
  runs: Run[]; compact?: boolean; selectable?: boolean; selected?: string[]; onSelect?: (id: string) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className={tableCls.table}>
        <thead>
          <tr>
            {selectable && <th className={cn(tableCls.th, "w-8")} />}
            <th className={tableCls.th}>Status</th>
            <th className={tableCls.th}>Workflow</th>
            {!compact && <th className={tableCls.th}>Objective</th>}
            <th className={cn(tableCls.th, "text-right")}>Duration</th>
            <th className={cn(tableCls.th, "text-right")}>Tokens</th>
            <th className={cn(tableCls.th, "text-right")}>Cost</th>
            <th className={cn(tableCls.th, "text-right")}>Started</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className={tableCls.tr}>
              {selectable && (
                <td className={tableCls.td}>
                  <input type="checkbox" aria-label={`Select run ${shortId(r.id)}`} checked={selected?.includes(r.id) ?? false} onChange={() => onSelect?.(r.id)} className="accent-[rgb(var(--ember))]" />
                </td>
              )}
              <td className={tableCls.td}><RunStatusBadge status={r.status} /></td>
              <td className={tableCls.td}>
                <Link href={`/runs/${r.id}`} className="font-medium text-fg hover:text-ember">
                  {r.workflowName}
                </Link>
                <span className="ml-2 inline-flex items-center gap-1 text-xs text-faint">
                  <span className="mono">{shortId(r.id, 6)}</span>
                  {r.version !== null && <span className="mono">v{r.version}</span>}
                  {r.parentRunId && <span title="Replay"><RotateCcw className="h-3 w-3" /></span>}
                  {r.replayConfig?.model && <span title="Model override" className="mono"><GitCompare className="inline h-3 w-3" /> {r.replayConfig.model}</span>}
                </span>
              </td>
              {!compact && <td className={cn(tableCls.td, "max-w-[280px] truncate text-muted")} title={r.input?.objective}>{r.input?.objective ?? r.goal}</td>}
              <td className={cn(tableCls.td, "num text-right")}>{fmtDuration(r.durationS)}</td>
              <td className={cn(tableCls.td, "num text-right")}>{r.totals.modelCalls ? fmtInt(r.totals.totalTokens) : <span className="text-faint">—</span>}</td>
              <td className={cn(tableCls.td, "text-right")}>{r.totals.modelCalls ? <Money value={r.totals.costUsd} basis={r.totals.costBasis} partial={r.totals.costPartial} /> : <span className="text-faint">—</span>}</td>
              <td className={cn(tableCls.td, "num text-right text-muted")}>{fmtRelative(r.createdAt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
