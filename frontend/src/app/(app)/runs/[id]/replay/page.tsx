"use client";
import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { RotateCcw } from "lucide-react";
import { RunStatusBadge } from "@/components/status";
import { Button, Checkbox, ErrorState, Field, Input, Kv, PageHeader, Panel, Select, Skeleton } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/overlay";
import { ApiError, api } from "@/lib/api";
import { useQuery } from "@/lib/hooks";
import { fmtDateTime, shortId } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Run, Workflow } from "@/lib/types";

type Mode = "same" | "different_model" | "new_version";

export default function ReplayPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { push } = useToast();
  const run = useQuery<Run>(`/api/runs/${id}`);
  const wf = useQuery<Workflow>(run.data?.workflowId ? `/api/workflows/${run.data.workflowId}` : null);
  const settings = useQuery<{ models: { routerTiers: Record<string, string> } }>("/api/settings");
  const [mode, setMode] = React.useState<Mode>("same");
  const [model, setModel] = React.useState("");
  const [versionId, setVersionId] = React.useState("");
  const [reuse, setReuse] = React.useState<string[]>([]);
  const [budget, setBudget] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const idem = React.useRef(crypto.randomUUID());

  if (run.error) return <div className="p-6"><ErrorState error={run.error} title="Run not found" /></div>;
  const r = run.data;
  if (!r) return <div className="p-6"><Skeleton className="h-64" /></div>;
  const models = [...new Set(Object.values(settings.data?.models.routerTiers ?? {}))];
  const usedModels = r.totals.byModel.map((m) => m.model);
  const approved = (wf.data?.versions ?? []).filter((v) => v.approved && v.id !== r.workflowVersionId);
  const reusable = (r.nodes ?? []).filter((n) => n.state === "SUCCESS" && n.type === "AGENT");

  async function go() {
    setBusy(true);
    try {
      const nr = await api.post<Run>(`/api/runs/${id}/replay`, {
        mode, model: mode === "different_model" ? model : null, workflow_version_id: mode === "new_version" ? versionId : null,
        reuse_nodes: reuse, budget_usd: budget ? Number(budget) : null,
      }, { "Idempotency-Key": idem.current });
      router.push(`/runs/${nr.id}`);
    } catch (e) { push({ tone: "bad", title: "Couldn't start the replay", body: (e as ApiError).message }); } finally { setBusy(false); }
  }

  const options: { id: Mode; title: string; body: string; disabled?: boolean }[] = [
    { id: "same", title: "Same configuration", body: "Identical workflow version, input, policies and routing. Checks reproducibility." },
    { id: "different_model", title: "Different model", body: "Same workflow and input; every agent is forced onto the model you choose." },
    { id: "new_version", title: "New workflow version", body: "Same input against another approved version of this workflow.", disabled: approved.length === 0 },
  ];

  return (
    <div className="mx-auto max-w-[980px] p-6">
      <PageHeader crumbs={<><Link href="/runs" className="hover:text-fg">Runs</Link> / <Link href={`/runs/${id}`} className="mono hover:text-fg">{shortId(id)}</Link></>}
        title="Replay run" subtitle="A replay is a new run created from the original's immutable configuration. The original run is never modified." />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel title="How to replay">
          <div className="space-y-2">
            {options.map((o) => (
              <label key={o.id} className={cn("flex cursor-pointer gap-3 rounded-md border p-3", mode === o.id ? "border-ember bg-ember/5" : "border-line hover:bg-hover/50", o.disabled && "pointer-events-none opacity-50")}>
                <input type="radio" name="mode" className="mt-1 accent-[rgb(var(--ember))]" checked={mode === o.id} onChange={() => setMode(o.id)} disabled={o.disabled} />
                <span><span className="block text-sm font-medium">{o.title}</span><span className="block text-xs text-muted">{o.body}{o.disabled ? " No other approved version exists." : ""}</span></span>
              </label>
            ))}
          </div>
          <div className="mt-4 space-y-4">
            {mode === "different_model" && <Field label="Model"><Select value={model} onChange={(e) => setModel(e.target.value)}><option value="">Choose…</option>{models.map((m) => <option key={m}>{m}</option>)}</Select></Field>}
            {mode === "new_version" && <Field label="Workflow version"><Select value={versionId} onChange={(e) => setVersionId(e.target.value)}><option value="">Choose…</option>{approved.map((v) => <option key={v.id} value={v.id}>v{v.version} · {fmtDateTime(v.createdAt)}</option>)}</Select></Field>}
            <Field label="Reuse outputs of successful steps" hint="Only steps whose definition is identical in the target version are reused; everything else re-executes.">
              <div className="grid gap-1.5 sm:grid-cols-2">
                {reusable.length === 0 && <span className="text-xs text-faint">No successful agent steps to reuse.</span>}
                {reusable.map((n) => <Checkbox key={n.id} checked={reuse.includes(n.id)} onChange={(v) => setReuse(v ? [...reuse, n.id] : reuse.filter((x) => x !== n.id))} label={<span className="mono text-xs">{n.id}</span>} />)}
              </div>
            </Field>
            <Field label="Budget cap (USD)" hint="Defaults to the original run's cap."><Input type="number" min={0} step={0.01} value={budget} onChange={(e) => setBudget(e.target.value)} placeholder={r.budget.maxUsd !== null ? String(r.budget.maxUsd) : "no cap"} /></Field>
          </div>
          <div className="mt-5 flex justify-end">
            <Button variant="primary" size="md" loading={busy} disabled={(mode === "different_model" && !model) || (mode === "new_version" && !versionId)} onClick={go}><RotateCcw className="h-3.5 w-3.5" /> Start replay</Button>
          </div>
        </Panel>
        <Panel title="Original run">
          <dl>
            <Kv k="Status"><RunStatusBadge status={r.status} /></Kv>
            <Kv k="Workflow">{r.workflowName}</Kv>
            <Kv k="Version" mono>v{r.version}</Kv>
            <Kv k="Models used" mono>{usedModels.join(", ") || "none"}</Kv>
            <Kv k="Repository" mono>{r.repositoryId ? shortId(r.repositoryId) : "none"}</Kv>
            <Kv k="Started">{fmtDateTime(r.startedAt)}</Kv>
          </dl>
          <p className="mt-3 text-xs text-faint">Input</p>
          <p className="mt-1 rounded border border-line bg-bg p-2 text-sm text-muted">{r.input?.objective}</p>
        </Panel>
      </div>
    </div>
  );
}
