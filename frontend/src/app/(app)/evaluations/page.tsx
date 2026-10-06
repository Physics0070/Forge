"use client";
import * as React from "react";
import Link from "next/link";
import { FlaskConical, Plus } from "lucide-react";
import { Money, RunStatusBadge } from "@/components/status";
import { Badge, Button, Checkbox, Empty, ErrorState, Field, Input, PageHeader, Panel, Select, Skeleton, Textarea, tableCls } from "@/components/ui/primitives";
import { Dialog, useToast } from "@/components/ui/overlay";
import { ApiError, api } from "@/lib/api";
import { useQuery } from "@/lib/hooks";
import { NA, fmtDuration, fmtInt, fmtPct, fmtRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Metrics, Project, Repository, Workflow } from "@/lib/types";

interface Evaluation { id: string; name: string; projectId: string; createdAt: string; task: { objective: string; forgeMode: string }; arms: { mode: string; runId: string | null; status: string; metrics: Metrics | null }[] }

const ARM_LABEL: Record<string, string> = { single_agent: "Single agent", manual_workflow: "Manual workflow", forge_workflow: "FORGE workflow" };
const METRICS: [keyof Metrics | "dup", string, (m: Metrics) => React.ReactNode][] = [
  ["success", "Task success", (m) => (m.success === null ? <span className="text-faint">in progress</span> : m.success ? <span className="text-ok">yes</span> : <span className="text-bad">no</span>)],
  ["schemaValidity", "Schema validity", (m) => fmtPct(m.schemaValidity)],
  ["verificationRate", "Verification rate", (m) => (m.verificationRate === null ? <span className="text-faint">{NA}</span> : fmtPct(m.verificationRate))],
  ["costUsd", "Cost", (m) => <Money value={m.costUsd} basis={m.costBasis} />],
  ["totalTokens", "Tokens", (m) => fmtInt(m.totalTokens)],
  ["durationS", "Latency (wall clock)", (m) => fmtDuration(m.durationS)],
  ["retries", "Retries", (m) => fmtInt(m.retries)],
  ["recoveryRate", "Recovery rate", (m) => (m.recoveryRate === null ? <span className="text-faint">{NA} (no failures)</span> : fmtPct(m.recoveryRate))],
  ["dup", "Duplicate work", (m) => (m.duplicateWork ? `${m.duplicateWork.duplicateToolCalls} of ${m.duplicateWork.totalToolCalls} tool calls` : <span className="text-faint">{NA}</span>)],
];

export default function EvaluationsPage() {
  const q = useQuery<{ evaluations: Evaluation[] }>("/api/evaluations", { every: 8000 });
  const [open, setOpen] = React.useState(false);
  return (
    <div className="mx-auto max-w-[1180px] p-6">
      <PageHeader title="Evaluations" subtitle="Run the same objective as a single agent, a manual workflow and a FORGE-generated workflow, then compare the recorded results. No benchmark number is ever typed in by hand."
        actions={<Button variant="primary" onClick={() => setOpen(true)}><Plus className="h-3.5 w-3.5" /> New evaluation</Button>} />
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} /> : !q.data ? <Skeleton className="h-40" /> : q.data.evaluations.length === 0 ? (
        <Panel><Empty icon={<FlaskConical className="h-6 w-6" />} title="No evaluations yet" action={<Button variant="primary" onClick={() => setOpen(true)}>Create one</Button>}>Pick an objective and a repository; FORGE starts one real run per arm.</Empty></Panel>
      ) : (
        <div className="space-y-4">
          {q.data.evaluations.map((ev) => (
            <Panel key={ev.id} title={ev.name} subtitle={`${ev.task.objective} · ${fmtRelative(ev.createdAt)}`} flush>
              <div className="overflow-x-auto">
                <table className={tableCls.table}>
                  <thead><tr><th className={tableCls.th}>Metric</th>{ev.arms.map((a) => (
                    <th key={a.mode} className={cn(tableCls.th, "text-right")}>
                      <span className="block">{ARM_LABEL[a.mode] ?? a.mode}{a.mode === "forge_workflow" && <Badge className="ml-1.5 normal-case">{ev.task.forgeMode}</Badge>}</span>
                      {a.runId && <Link href={`/runs/${a.runId}`} className="mt-1 inline-block"><RunStatusBadge status={a.status as any} /></Link>}
                    </th>))}</tr></thead>
                  <tbody>{METRICS.map(([k, label, fmt]) => (
                    <tr key={String(k)} className={tableCls.tr}>
                      <td className={tableCls.td}>{label}</td>
                      {ev.arms.map((a) => <td key={a.mode} className={cn(tableCls.td, "num text-right")}>{a.metrics ? fmt(a.metrics) : <span className="text-faint">{NA}</span>}</td>)}
                    </tr>))}</tbody>
                </table>
              </div>
            </Panel>
          ))}
        </div>
      )}
      <NewEvaluation open={open} onOpenChange={setOpen} onCreated={q.reload} />
    </div>
  );
}

function NewEvaluation({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: () => void }) {
  const projects = useQuery<{ projects: Project[] }>(open ? "/api/projects" : null);
  const [project, setProject] = React.useState("");
  const repos = useQuery<{ repositories: Repository[] }>(project ? `/api/projects/${project}/repositories` : null);
  const wfs = useQuery<{ workflows: Workflow[] }>(project ? `/api/workflows?project_id=${project}` : null);
  const [name, setName] = React.useState("Single agent vs FORGE");
  const [objective, setObjective] = React.useState("Audit this repository for security vulnerabilities and report only what you can support with evidence.");
  const [repo, setRepo] = React.useState("");
  const [arms, setArms] = React.useState<string[]>(["single_agent", "forge_workflow"]);
  const [manual, setManual] = React.useState("");
  const [mode, setMode] = React.useState("template");
  const [budget, setBudget] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const { push } = useToast();
  React.useEffect(() => { if (!project && projects.data?.projects[0]) setProject(projects.data.projects[0].id); }, [projects.data, project]);
  const versions = (wfs.data?.workflows ?? []).filter((w) => w.templateKey !== "eval").flatMap((w) => w.versions.filter((v) => v.approved).map((v) => ({ id: v.id, label: `${w.name} v${v.version}` })));
  const toggle = (a: string) => setArms(arms.includes(a) ? arms.filter((x) => x !== a) : [...arms, a]);
  async function create() {
    setBusy(true);
    try {
      await api.post("/api/evaluations", { project_id: project, name, objective, repository_id: repo || null, arms, manual_workflow_version_id: manual || null, forge_mode: mode, budget_usd: budget ? Number(budget) : null });
      onOpenChange(false);
      onCreated();
      push({ tone: "ok", title: "Evaluation started", body: `${arms.length} real run(s) launched.` });
    } catch (e) { push({ tone: "bad", title: "Couldn't start the evaluation", body: (e as ApiError).message }); } finally { setBusy(false); }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="New evaluation" width="max-w-xl" description="Each arm is a real run on the same objective and repository."
      footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" loading={busy} disabled={!project || arms.length === 0 || (arms.includes("manual_workflow") && !manual)} onClick={create}>Start runs</Button></>}>
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Project"><Select value={project} onChange={(e) => { setProject(e.target.value); setRepo(""); }}>{(projects.data?.projects ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select></Field>
        </div>
        <Field label="Objective"><Textarea rows={3} value={objective} onChange={(e) => setObjective(e.target.value)} /></Field>
        <Field label="Repository"><Select value={repo} onChange={(e) => setRepo(e.target.value)}><option value="">None</option>{(repos.data?.repositories ?? []).filter((r) => r.status === "ready").map((r) => <option key={r.id} value={r.id}>{r.url ?? r.kind}</option>)}</Select></Field>
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted">Arms</p>
          <Checkbox checked={arms.includes("single_agent")} onChange={() => toggle("single_agent")} label="Single agent (one generalist agent, all read tools)" />
          <Checkbox checked={arms.includes("manual_workflow")} onChange={() => toggle("manual_workflow")} label="Manual workflow (an approved version you choose)" />
          {arms.includes("manual_workflow") && <Select value={manual} onChange={(e) => setManual(e.target.value)}><option value="">Choose a version…</option>{versions.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}</Select>}
          <Checkbox checked={arms.includes("forge_workflow")} onChange={() => toggle("forge_workflow")} label="FORGE workflow" />
          {arms.includes("forge_workflow") && <Select value={mode} onChange={(e) => setMode(e.target.value)}><option value="template">security-audit template (analysis only)</option><option value="compile">compiled from the objective by Nemotron (read-only)</option></Select>}
        </div>
        <Field label="Budget cap per run (USD)"><Input type="number" min={0} step={0.01} value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="no cap" /></Field>
      </div>
    </Dialog>
  );
}
