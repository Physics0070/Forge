"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { Play } from "lucide-react";
import { Button, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { Dialog, useToast } from "@/components/ui/overlay";
import { ApiError, api } from "@/lib/api";
import { useQuery } from "@/lib/hooks";
import type { Repository, Run } from "@/lib/types";

export function ExecuteDialog({ open, onOpenChange, versionId, projectId, goal, needsRepo }: {
  open: boolean; onOpenChange: (o: boolean) => void; versionId: string; projectId: string; goal: string; needsRepo: boolean;
}) {
  const router = useRouter();
  const { push } = useToast();
  const repos = useQuery<{ repositories: Repository[] }>(open ? `/api/projects/${projectId}/repositories` : null);
  const ready = (repos.data?.repositories ?? []).filter((r) => r.status === "ready");
  const [objective, setObjective] = React.useState(goal);
  const [repoId, setRepoId] = React.useState("");
  const [usd, setUsd] = React.useState("");
  const [tokens, setTokens] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  // One key per dialog session: double-clicks and network retries can never start two runs.
  const idem = React.useRef("");
  React.useEffect(() => { if (open) { idem.current = crypto.randomUUID(); setObjective(goal); } }, [open, goal]);
  React.useEffect(() => { if (!repoId && ready.length) setRepoId(ready[0].id); }, [ready, repoId]);

  async function start() {
    setBusy(true);
    try {
      const run = await api.post<Run>("/api/runs", {
        workflow_version_id: versionId, input: { objective }, repository_id: repoId || null,
        budget_usd: usd ? Number(usd) : null, budget_tokens: tokens ? Number(tokens) : null,
      }, { "Idempotency-Key": idem.current });
      onOpenChange(false);
      router.push(`/runs/${run.id}`);
    } catch (e) {
      push({ tone: "bad", title: "Couldn't start the run", body: (e as ApiError).message });
    } finally { setBusy(false); }
  }

  const blocked = needsRepo && !repoId;
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Execute workflow" description="Runs this exact, approved version. Nothing is applied to your repository."
      footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" loading={busy} disabled={objective.trim().length < 3 || blocked} onClick={start}><Play className="h-3.5 w-3.5" /> Start run</Button></>}>
      <div className="space-y-4">
        <Field label="Objective"><Textarea rows={3} value={objective} onChange={(e) => setObjective(e.target.value)} /></Field>
        <Field label="Repository" error={blocked ? "This workflow reads a repository. Connect one on the project page first." : null}>
          <Select value={repoId} onChange={(e) => setRepoId(e.target.value)}>
            <option value="">{ready.length ? "None" : "No repository connected"}</option>
            {ready.map((r) => <option key={r.id} value={r.id}>{(r.url ?? r.kind)} · {r.fileCount} files</option>)}
          </Select>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Budget cap (USD)" hint="Execution stops before a call that would exceed it."><Input type="number" min={0} step={0.01} value={usd} onChange={(e) => setUsd(e.target.value)} placeholder="no cap" /></Field>
          <Field label="Budget cap (tokens)" hint="Enforced even when no price is configured."><Input type="number" min={1} value={tokens} onChange={(e) => setTokens(e.target.value)} placeholder="no cap" /></Field>
        </div>
      </div>
    </Dialog>
  );
}
