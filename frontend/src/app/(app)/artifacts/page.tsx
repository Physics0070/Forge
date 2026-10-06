"use client";
import * as React from "react";
import Link from "next/link";
import { Package } from "lucide-react";
import { ArtifactInspector, ARTIFACT_LABEL } from "@/components/artifact-inspector";
import { Empty, ErrorState, PageHeader, Panel, Select, Skeleton, tableCls } from "@/components/ui/primitives";
import { qs } from "@/lib/api";
import { useQuery } from "@/lib/hooks";
import { fmtBytes, fmtRelative, shortId } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ArtifactMeta, Project } from "@/lib/types";

export default function ArtifactsPage() {
  const [project, setProject] = React.useState("");
  const [type, setType] = React.useState("");
  const [open, setOpen] = React.useState<string | null>(null);
  const projects = useQuery<{ projects: Project[] }>("/api/projects");
  const q = useQuery<{ artifacts: ArtifactMeta[] }>(`/api/artifacts${qs({ project_id: project, type, limit: 300 })}`);
  return (
    <div className="mx-auto max-w-[1180px] p-6">
      <PageHeader title="Artifacts" subtitle="Typed, content-hashed outputs with provenance: who produced them, with which model, from which inputs."
        actions={<>
          <Select aria-label="Project" value={project} onChange={(e) => setProject(e.target.value)} className="w-48"><option value="">All projects</option>{(projects.data?.projects ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select>
          <Select aria-label="Type" value={type} onChange={(e) => setType(e.target.value)} className="w-48"><option value="">All types</option>{Object.entries(ARTIFACT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>
        </>} />
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} /> : (
        <Panel flush>
          {!q.data ? <Skeleton className="m-3 h-24" /> : q.data.artifacts.length === 0 ? <Empty icon={<Package className="h-6 w-6" />} title="No artifacts">Artifacts are produced by runs: findings, evidence, verification results, patches and reports.</Empty> : (
            <table className={tableCls.table}>
              <thead><tr><th className={tableCls.th}>Type</th><th className={tableCls.th}>Run</th><th className={tableCls.th}>Node</th><th className={tableCls.th}>Producer</th><th className={tableCls.th}>sha256</th><th className={cn(tableCls.th, "text-right")}>Size</th><th className={cn(tableCls.th, "text-right")}>Created</th></tr></thead>
              <tbody>{q.data.artifacts.map((a) => (
                <tr key={a.id} className={cn(tableCls.tr, "cursor-pointer")} onClick={() => setOpen(a.id)}>
                  <td className={tableCls.td}><span className="font-medium text-info">{ARTIFACT_LABEL[a.type] ?? a.type}</span></td>
                  <td className={tableCls.td}>{a.runId ? <Link onClick={(e) => e.stopPropagation()} href={`/runs/${a.runId}`} className="mono text-xs hover:text-ember">{shortId(a.runId)}</Link> : "—"}</td>
                  <td className={cn(tableCls.td, "mono text-xs")}>{a.nodeId ?? "—"}</td>
                  <td className={cn(tableCls.td, "mono text-xs text-muted")}>{a.provenance?.producer ?? a.provenance?.tool ?? "—"}</td>
                  <td className={cn(tableCls.td, "mono text-xs text-faint")}>{a.contentHash.slice(0, 12)}</td>
                  <td className={cn(tableCls.td, "num text-right")}>{fmtBytes(a.sizeBytes)}</td>
                  <td className={cn(tableCls.td, "num text-right text-muted")}>{fmtRelative(a.createdAt)}</td>
                </tr>))}</tbody>
            </table>
          )}
        </Panel>
      )}
      <ArtifactInspector id={open} onClose={() => setOpen(null)} />
    </div>
  );
}
