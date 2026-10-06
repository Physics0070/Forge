"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { AlertOctagon, CheckCircle2, Sparkles, FileCode2 } from "lucide-react";
import { Badge, Button, Checkbox, Field, Select, Textarea } from "@/components/ui/primitives";
import { Dialog, Tabs, useToast } from "@/components/ui/overlay";
import { NODE_TYPE_META } from "@/components/status";
import { ApiError, api } from "@/lib/api";
import { useQuery } from "@/lib/hooks";
import type { IR, Issue, Repository, Workflow } from "@/lib/types";

interface CompileOk { workflow: IR; issues: Issue[]; meta: { model: string; tokens: number; attempts: number } }
interface CompileFail { code: string; status?: string; reason?: string; suggestion?: string; issues?: Issue[]; message: string }

export function NewWorkflowDialog({ open, onOpenChange, projectId }: { open: boolean; onOpenChange: (o: boolean) => void; projectId: string }) {
  const router = useRouter();
  const { push } = useToast();
  const [mode, setMode] = React.useState<"template" | "compile">("template");
  const [goal, setGoal] = React.useState("Audit this repository for security vulnerabilities, verify each finding against the code, and propose fixes for the ones that are real.");
  const [remediation, setRemediation] = React.useState(true);
  const [allowWrites, setAllowWrites] = React.useState(true);
  const [repoId, setRepoId] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [compiled, setCompiled] = React.useState<CompileOk | null>(null);
  const [fail, setFail] = React.useState<CompileFail | null>(null);
  const repos = useQuery<{ repositories: Repository[] }>(open ? `/api/projects/${projectId}/repositories` : null);
  const ready = (repos.data?.repositories ?? []).filter((r) => r.status === "ready");

  React.useEffect(() => { if (!open) { setCompiled(null); setFail(null); } }, [open]);

  async function createFrom(body: object) {
    const w = await api.post<Workflow>("/api/workflows", { project_id: projectId, ...body });
    push({ tone: "ok", title: "Workflow created", body: "Review it, edit anything, then approve it to run." });
    onOpenChange(false);
    router.push(`/workflows/${w.id}`);
  }

  async function go() {
    setBusy(true);
    setFail(null);
    try {
      if (mode === "template") {
        await createFrom({ template: "repository-security-audit", goal, include_remediation: remediation });
      } else if (!compiled) {
        setCompiled(await api.post<CompileOk>("/api/workflows/compile", { project_id: projectId, goal, repository_id: repoId || null, constraints: { allow_writes: allowWrites } }));
      } else {
        await createFrom({ ir: compiled.workflow });
      }
    } catch (e) {
      const a = e as ApiError;
      if (a.code === "COMPILATION_FAILED") setFail(a.body as CompileFail);
      else push({ tone: "bad", title: "Couldn't create the workflow", body: a.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="New workflow"
      description="FORGE compiles an objective into a typed graph you can inspect and edit before anything runs."
      width="max-w-2xl"
      footer={
        <>
          {compiled && <Button variant="ghost" onClick={() => setCompiled(null)}>Back</Button>}
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" onClick={go} loading={busy} disabled={goal.trim().length < 8}>
            {mode === "template" ? "Create from template" : compiled ? "Inspect & save" : <><Sparkles className="h-3.5 w-3.5" /> Compile with Nemotron</>}
          </Button>
        </>
      }
    >
      <Tabs tabs={[{ id: "template", label: "Security audit template" }, { id: "compile", label: "Compile from goal" }]} value={mode} onChange={(m) => { setMode(m); setCompiled(null); setFail(null); }} className="mb-4" />

      {!compiled && (
        <div className="space-y-4">
          <Field label="Objective" hint={mode === "compile" ? "Describe the outcome. Nemotron picks agents, dependencies, parallelism, tools and verification." : "The same objective is given to every agent in the audit."}>
            <Textarea rows={4} value={goal} onChange={(e) => setGoal(e.target.value)} />
          </Field>
          {mode === "template" ? (
            <>
              <Checkbox checked={remediation} onChange={setRemediation} label={<span>Include remediation <span className="text-faint">(proposed patches behind two human approval gates, applied only in an isolated copy)</span></span>} />
              <div className="rounded border border-line bg-raised/50 p-3 text-xs text-muted">
                <p className="mb-1 flex items-center gap-1.5 font-medium text-fg"><FileCode2 className="h-3.5 w-3.5 text-ember" /> What you get</p>
                Plan → parallel code / dependency / research scans → graceful-degradation recovery → risk prioritisation → <b>independent verification</b> →{remediation ? " fix proposals → approval → sandboxed tests → approval →" : ""} report. It is ordinary workflow IR; you can edit every node.
              </div>
            </>
          ) : (
            <>
              <Field label="Repository context (optional)" hint="Only a content-free summary (file counts, extensions) is shown to the planner.">
                <Select value={repoId} onChange={(e) => setRepoId(e.target.value)}>
                  <option value="">None</option>
                  {ready.map((r) => <option key={r.id} value={r.id}>{r.url ?? r.kind} · {r.fileCount} files</option>)}
                </Select>
              </Field>
              <Checkbox checked={allowWrites} onChange={setAllowWrites} label={<span>Allow code-modifying steps <span className="text-faint">(always gated behind a human approval node)</span></span>} />
            </>
          )}
          {fail && (
            <div role="alert" className="rounded-md border border-bad/40 bg-bad/5 p-3.5">
              <p className="flex items-center gap-2 text-sm font-semibold text-bad"><AlertOctagon className="h-4 w-4" /> COMPILATION FAILED</p>
              <dl className="mt-2 space-y-1.5 text-sm">
                <div><dt className="text-xs text-faint">Reason</dt><dd>{fail.reason ?? fail.message}</dd></div>
                {fail.suggestion && <div><dt className="text-xs text-faint">Suggested correction</dt><dd className="text-muted">{fail.suggestion}</dd></div>}
              </dl>
              {fail.issues && fail.issues.length > 0 && (
                <ul className="mt-2 space-y-1 border-t border-bad/20 pt-2 text-xs text-muted">
                  {fail.issues.slice(0, 6).map((i, k) => <li key={k}><span className="mono text-bad">{i.code}</span> {i.message}</li>)}
                </ul>
              )}
            </div>
          )}
        </div>
      )}

      {compiled && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <CheckCircle2 className="h-4 w-4 text-ok" /> <span className="font-medium">{compiled.workflow.name}</span>
            <Badge>{compiled.workflow.nodes.length} nodes</Badge><Badge>{compiled.workflow.edges.length} edges</Badge>
            <Badge tone="info" className="normal-case">{compiled.meta.model}</Badge>
            <span className="num text-xs text-faint">{compiled.meta.tokens.toLocaleString()} tokens · {compiled.meta.attempts} attempt{compiled.meta.attempts > 1 ? "s" : ""}</span>
          </div>
          <p className="text-sm text-muted">{compiled.workflow.description}</p>
          <ul className="divide-y divide-line rounded border border-line">
            {compiled.workflow.nodes.map((n) => {
              const M = NODE_TYPE_META[n.type];
              return (
                <li key={n.id} className="flex items-center gap-3 px-3 py-2">
                  <M.icon className="h-4 w-4 text-faint" />
                  <span className="min-w-0 flex-1 truncate text-sm">{n.name || n.id}</span>
                  <span className="mono text-xs text-faint">{n.agentId ?? M.label}</span>
                  {n.tools && n.tools.length > 0 && <span className="text-xs text-faint">{n.tools.length} tools</span>}
                </li>
              );
            })}
          </ul>
          {compiled.issues.length > 0 && <p className="text-xs text-warn">{compiled.issues.length} warning(s). You can review them in the editor.</p>}
        </div>
      )}
    </Dialog>
  );
}
