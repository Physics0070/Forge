"use client";
import * as React from "react";
import Link from "next/link";
import { Download, ExternalLink, Fingerprint } from "lucide-react";
import { Drawer } from "@/components/ui/overlay";
import { Badge, Button, CopyButton, ErrorState, Kv, Skeleton } from "@/components/ui/primitives";
import { VerificationBadge } from "@/components/status";
import { CodeBlock, DiffView, JsonTree, Markdown, langOf } from "@/components/viz";
import { useQuery } from "@/lib/hooks";
import { fmtBytes, fmtDateTime, shortId } from "@/lib/format";
import type { ArtifactMeta } from "@/lib/types";

export const ARTIFACT_LABEL: Record<string, string> = {
  security_findings: "Security findings", evidence: "Evidence", verification: "Verification", fix_proposals: "Fix proposals", patch: "Patch (diff)",
  applied_patch: "Applied patch", test_report: "Test report", report: "Report", plan: "Scan plan", node_output: "Node output",
};

export function ArtifactBody({ a }: { a: ArtifactMeta }) {
  const c = a.content;
  if (c === null || c === undefined) return <p className="text-sm text-faint">No content.</p>;
  if (a.contentType === "text/x-diff" || a.type === "patch" || a.type === "applied_patch") return <DiffView patch={String(c)} />;
  if (a.type === "report" && typeof c === "object" && typeof (c as any).markdown === "string") return <Markdown>{(c as any).markdown}</Markdown>;
  if (typeof c === "string") return <CodeBlock code={c} lang={langOf(a.provenance?.path ?? "")} lineNumbers />;
  if (a.type === "evidence" && typeof c === "object") {
    const e = c as Record<string, string>;
    return (
      <div className="space-y-3">
        <dl className="rounded border border-line bg-raised/40 px-3 py-1">
          <Kv k="Source">{e.source}</Kv><Kv k="Agent" mono>{e.agent}</Kv><Kv k="Query">{e.query}</Kv><Kv k="Retrieved">{fmtDateTime(e.timestamp)}</Kv>
        </dl>
        <a href={e.url} target="_blank" rel="noopener noreferrer nofollow" className="inline-flex items-center gap-1.5 break-all text-sm text-info hover:underline"><ExternalLink className="h-3.5 w-3.5 shrink-0" />{e.url}</a>
        <blockquote className="border-l-2 border-ember pl-3 text-sm text-muted">{e.snippet}</blockquote>
      </div>
    );
  }
  if (a.type === "fix_proposals" && typeof c === "object" && Array.isArray((c as any).proposals)) {
    return (
      <div className="space-y-4">
        {(c as any).proposals.map((p: any, i: number) => (
          <div key={i} className="space-y-2">
            <div className="flex items-center gap-2"><span className="mono text-xs text-muted">{p.finding_id}</span><Badge tone={p.risk === "high" ? "bad" : p.risk === "medium" ? "warn" : "ok"}>{p.risk} risk</Badge></div>
            <p className="text-sm">{p.summary}</p>
            {p.patch ? <DiffView patch={p.patch} maxHeight={300} /> : <p className="text-xs text-faint">No patch: needs a human decision.</p>}
          </div>
        ))}
      </div>
    );
  }
  return <JsonTree data={c} maxHeight={560} />;
}

export function ArtifactInspector({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useQuery<ArtifactMeta>(id ? `/api/artifacts/${id}` : null);
  const a = q.data;
  return (
    <Drawer open={!!id} onClose={onClose} width="w-[640px]" title={a ? (ARTIFACT_LABEL[a.type] ?? a.type) : "Artifact"} subtitle={a ? `${a.schema} · ${fmtBytes(a.sizeBytes)}` : undefined}>
      {q.error ? <div className="p-4"><ErrorState error={q.error} onRetry={q.reload} /></div> : !a ? <div className="space-y-3 p-4"><Skeleton className="h-24" /><Skeleton className="h-48" /></div> : (
        <div>
          <div className="space-y-3 border-b border-line p-4">
            <dl className="rounded border border-line bg-raised/40 px-3 py-1">
              <Kv k="Produced by" mono>{a.provenance?.producer ?? "—"}{a.provenance?.model ? ` · ${a.provenance.model}` : ""}</Kv>
              <Kv k="Node" mono>{a.nodeId ?? "—"}</Kv>
              <Kv k="Run">{a.runId ? <Link className="mono text-xs text-info hover:underline" href={`/runs/${a.runId}`}>{shortId(a.runId)}</Link> : "—"}</Kv>
              <Kv k="Created">{fmtDateTime(a.createdAt)}</Kv>
              <Kv k="Inputs from" mono>{(a.provenance?.inputs_from ?? []).join(", ") || "—"}</Kv>
            </dl>
            <div className="flex items-center justify-between gap-2">
              <span className="flex min-w-0 items-center gap-1.5 text-xs text-faint"><Fingerprint className="h-3.5 w-3.5 shrink-0" /><span className="mono truncate" title={a.contentHash}>sha256:{a.contentHash.slice(0, 20)}…</span><CopyButton text={a.contentHash} /></span>
              <a href={`/api/artifacts/${a.id}/download`}><Button size="xs"><Download className="h-3 w-3" /> Download</Button></a>
            </div>
            {a.verification && a.verification.length > 0 && (
              <div className="flex flex-wrap gap-1.5">{a.verification.map((v) => <span key={v.findingId} title={v.reason}><VerificationBadge status={v.status} /> <span className="mono text-2xs text-faint">{v.findingId}</span></span>)}</div>
            )}
          </div>
          <div className="p-4"><ArtifactBody a={a} /></div>
        </div>
      )}
    </Drawer>
  );
}
