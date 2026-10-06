"use client";
import Link from "next/link";
import { Badge, Empty, tableCls } from "@/components/ui/primitives";
import { fmtRelative, shortId } from "@/lib/format";
import type { Workflow } from "@/lib/types";
import { cn } from "@/lib/utils";

export function WorkflowsTable({ workflows, empty }: { workflows: Workflow[]; empty?: React.ReactNode }) {
  if (workflows.length === 0) return <>{empty ?? <Empty title="No workflows yet" />}</>;
  return (
    <div className="overflow-x-auto">
      <table className={tableCls.table}>
        <thead>
          <tr>
            <th className={tableCls.th}>Workflow</th>
            <th className={tableCls.th}>Version</th>
            <th className={tableCls.th}>Status</th>
            <th className={cn(tableCls.th, "text-right")}>Nodes</th>
            <th className={tableCls.th}>Origin</th>
            <th className={cn(tableCls.th, "text-right")}>Created</th>
          </tr>
        </thead>
        <tbody>
          {workflows.map((w) => (
            <tr key={w.id} className={tableCls.tr}>
              <td className={tableCls.td}>
                <Link href={`/workflows/${w.id}`} className="font-medium hover:text-ember">{w.name}</Link>
                <span className="mono ml-2 text-xs text-faint">{shortId(w.id, 6)}</span>
              </td>
              <td className={cn(tableCls.td, "mono text-xs")}>v{w.latestVersion?.version} <span className="text-faint">of {w.versions.length}</span></td>
              <td className={tableCls.td}>{w.latestVersion?.approved ? <Badge tone="ok">approved</Badge> : <Badge tone="warn">draft</Badge>}</td>
              <td className={cn(tableCls.td, "num text-right")}>{w.latestVersion?.nodeCount ?? "—"}</td>
              <td className={cn(tableCls.td, "text-muted")}>{w.templateKey === "eval" ? "evaluation" : w.templateKey ? "template" : w.latestVersion?.compilerMeta?.model ? "compiled" : "manual"}</td>
              <td className={cn(tableCls.td, "num text-right text-muted")}>{fmtRelative(w.createdAt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
