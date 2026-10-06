"use client";
import * as React from "react";
import { Hand } from "lucide-react";
import { Badge, Button, Textarea } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/overlay";
import { DiffView, JsonTree } from "@/components/viz";
import { ApiError, api } from "@/lib/api";
import type { Approval } from "@/lib/types";

function Payload({ payload }: { payload: any }) {
  if (!payload) return null;
  if (Array.isArray(payload.proposals)) {
    return (
      <div className="space-y-4">
        {payload.proposals.length === 0 && <p className="text-sm text-faint">No proposals.</p>}
        {payload.proposals.map((p: any, i: number) => (
          <div key={i} className="space-y-2 rounded border border-line bg-bg p-3">
            <div className="flex items-center gap-2"><span className="mono text-xs text-muted">{p.finding_id}</span><Badge tone={p.risk === "high" ? "bad" : p.risk === "medium" ? "warn" : "ok"}>{p.risk} risk</Badge><span className="mono text-xs text-faint">{(p.files ?? []).join(", ")}</span></div>
            <p className="text-sm">{p.summary}</p>
            {p.patch ? <DiffView patch={p.patch} maxHeight={260} /> : <p className="text-xs text-faint">No automatic patch: this needs a human decision.</p>}
          </div>
        ))}
      </div>
    );
  }
  if (typeof payload.diff === "string") {
    const t = payload.tests ?? {};
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-sm"><span className="text-muted">Tests:</span><Badge tone={t.status === "PASSED" ? "ok" : t.status === "FAILED" ? "bad" : "warn"}>{t.status ?? "NOT AVAILABLE"}</Badge>{t.command && <span className="mono text-xs text-faint">{t.command}</span>}</div>
        {t.status === "NOT_AVAILABLE" && <p className="text-xs text-warn">The sandbox could not run the tests. Review the diff carefully: nothing was verified by execution.</p>}
        {t.output_excerpt && <pre className="mono max-h-32 overflow-auto rounded border border-line bg-bg p-2 text-xs text-muted">{t.output_excerpt}</pre>}
        <p className="text-xs text-faint">Diff computed by FORGE from the isolated workspace (not reported by the agent):</p>
        <DiffView patch={payload.diff} maxHeight={320} />
      </div>
    );
  }
  return <JsonTree data={payload} maxHeight={260} />;
}

export function ApprovalCard({ approval, onDone }: { approval: Approval; onDone: () => void }) {
  const [note, setNote] = React.useState("");
  const [busy, setBusy] = React.useState<"grant" | "reject" | null>(null);
  const { push } = useToast();
  async function decide(decision: "grant" | "reject") {
    setBusy(decision);
    try {
      await api.post(`/api/approvals/${approval.id}`, { decision, note: note || null });
      push({ tone: decision === "grant" ? "ok" : "info", title: decision === "grant" ? "Approved" : "Rejected" });
      onDone();
    } catch (e) {
      push({ tone: "bad", title: "Couldn't record the decision", body: (e as ApiError).message });
    } finally { setBusy(null); }
  }
  return (
    <section className="rounded-md border border-warn/50 bg-warn/[0.04]" aria-label="Approval required">
      <header className="flex items-center gap-2.5 border-b border-warn/30 px-4 py-2.5">
        <Hand className="h-4 w-4 text-warn" />
        <div>
          <h3 className="text-sm font-semibold">{approval.request.title ?? "Approval required"}</h3>
          {approval.request.summary && <p className="text-xs text-muted">{approval.request.summary}</p>}
        </div>
        <Badge tone="warn" className="ml-auto">execution paused here</Badge>
      </header>
      <div className="p-4"><Payload payload={approval.request.payload} /></div>
      <footer className="flex flex-wrap items-end gap-3 border-t border-warn/30 px-4 py-3">
        <div className="min-w-[220px] flex-1"><Textarea rows={1} placeholder="Optional note for the audit log" value={note} onChange={(e) => setNote(e.target.value)} className="min-h-[32px]" /></div>
        <Button variant="danger" loading={busy === "reject"} disabled={!!busy} onClick={() => decide("reject")}>Reject</Button>
        <Button variant="ok" loading={busy === "grant"} disabled={!!busy} onClick={() => decide("grant")}>Approve</Button>
      </footer>
    </section>
  );
}
