"use client";
import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Github, GitBranch, Loader2, Trash2, Upload, Workflow as WorkflowIcon } from "lucide-react";
import { RunsTable } from "@/components/runs-table";
import { NewWorkflowDialog } from "@/components/new-workflow";
import { Badge, Button, Empty, ErrorState, Field, Input, Kv, PageHeader, Panel, Skeleton } from "@/components/ui/primitives";
import { Dialog, useToast } from "@/components/ui/overlay";
import { ApiError, api } from "@/lib/api";
import { useQuery } from "@/lib/hooks";
import { fmtBytes, fmtInt, fmtRelative, shortId } from "@/lib/format";
import type { Project, Repository, Run, Workflow } from "@/lib/types";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const project = useQuery<Project>(`/api/projects/${id}`);
  const repos = useQuery<{ repositories: Repository[] }>(`/api/projects/${id}/repositories`, { every: 2500 });
  const wfs = useQuery<{ workflows: Workflow[] }>(`/api/workflows?project_id=${id}`);
  const runs = useQuery<{ runs: Run[] }>(`/api/runs?project_id=${id}&limit=10`, { every: 8000 });
  const { push } = useToast();
  const [ghOpen, setGhOpen] = React.useState(false);
  const [nwOpen, setNwOpen] = React.useState(false);
  const [url, setUrl] = React.useState("");
  const [ref, setRef] = React.useState("");
  const [token, setToken] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const fileRef = React.useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = React.useState(false);

  if (project.error) return <div className="p-6"><ErrorState error={project.error} onRetry={project.reload} title="Project not found" /></div>;

  async function importGh() {
    setBusy(true);
    try {
      await api.post(`/api/projects/${id}/repositories/github`, { url, ref: ref || null, token: token || null });
      setGhOpen(false);
      setUrl(""); setRef(""); setToken("");
      repos.reload();
      push({ tone: "info", title: "Import started", body: "Cloning in an isolated step. Credentials are never stored." });
    } catch (e) {
      push({ tone: "bad", title: "Import failed", body: (e as ApiError).message });
    } finally { setBusy(false); }
  }
  async function upload(f: File) {
    setUploading(true);
    try {
      await api.upload(`/api/projects/${id}/repositories/zip`, f);
      repos.reload();
      push({ tone: "ok", title: "Repository uploaded" });
    } catch (e) {
      push({ tone: "bad", title: "Upload rejected", body: (e as ApiError).message });
    } finally { setUploading(false); if (fileRef.current) fileRef.current.value = ""; }
  }
  async function remove(rid: string) {
    try { await api.del(`/api/repositories/${rid}`); repos.reload(); } catch (e) { push({ tone: "bad", title: "Couldn't remove", body: (e as ApiError).message }); }
  }

  const list = repos.data?.repositories;

  return (
    <div className="mx-auto max-w-[1180px] p-6">
      <PageHeader
        crumbs={<Link href="/projects" className="hover:text-fg">Projects</Link>}
        title={project.data?.name ?? "…"}
        subtitle={project.data?.description}
        actions={<><Link href={`/projects/${id}/workflows`}><Button>All workflows</Button></Link><Button variant="primary" onClick={() => setNwOpen(true)}><WorkflowIcon className="h-3.5 w-3.5" /> New workflow</Button></>}
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-4">
          <Panel title="Workflows" flush actions={<Link className="text-xs text-muted hover:text-fg" href={`/projects/${id}/workflows`}>Manage</Link>}>
            {!wfs.data ? <Skeleton className="m-3 h-16" /> : wfs.data.workflows.length === 0 ? (
              <Empty title="No workflows yet" action={<Button variant="primary" onClick={() => setNwOpen(true)}>Create the first workflow</Button>}>Generate one from an objective, or start from the security-audit template.</Empty>
            ) : (
              <ul className="divide-y divide-line">
                {wfs.data.workflows.slice(0, 6).map((w) => (
                  <li key={w.id}>
                    <Link href={`/workflows/${w.id}`} className="flex items-center gap-3 px-3.5 py-2.5 hover:bg-hover/60">
                      <WorkflowIcon className="h-4 w-4 text-faint" />
                      <span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{w.name}</span><span className="block truncate text-xs text-faint">{w.latestVersion?.nodeCount ?? 0} nodes · created {fmtRelative(w.createdAt)}</span></span>
                      <span className="mono text-xs text-muted">v{w.latestVersion?.version}</span>
                      {w.latestVersion?.approved ? <Badge tone="ok">approved</Badge> : <Badge>draft</Badge>}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="Recent runs" flush>
            {!runs.data ? <Skeleton className="m-3 h-16" /> : runs.data.runs.length === 0 ? <Empty title="No runs yet">Approve a workflow version and execute it.</Empty> : <RunsTable runs={runs.data.runs} compact />}
          </Panel>
        </div>

        <Panel title="Repository" subtitle="Isolated copy per run. Upstream is never modified." flush
          actions={<><Button size="xs" onClick={() => setGhOpen(true)}><Github className="h-3 w-3" /> GitHub</Button><Button size="xs" loading={uploading} onClick={() => fileRef.current?.click()}><Upload className="h-3 w-3" /> ZIP</Button>
            <input ref={fileRef} type="file" accept=".zip,application/zip" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></>}>
          {!list ? <Skeleton className="m-3 h-16" /> : list.length === 0 ? (
            <Empty icon={<GitBranch className="h-5 w-5" />} title="No repository connected">Connect a public GitHub repository or upload a ZIP (max 100 MB).</Empty>
          ) : (
            <ul className="divide-y divide-line">
              {list.map((r) => (
                <li key={r.id} className="space-y-2 p-3.5">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{r.kind === "github" ? r.url?.replace("https://github.com/", "") : r.url}</p>
                      <p className="mono text-xs text-faint">{shortId(r.id)} · {r.kind}{r.ref ? ` · ${r.ref}` : ""}{r.commitSha ? ` · ${r.commitSha.slice(0, 7)}` : ""}</p>
                    </div>
                    <div className="flex items-center gap-1">
                      {r.status === "importing" ? <Badge tone="warn"><Loader2 className="h-3 w-3 animate-spin" /> importing</Badge> : r.status === "ready" ? <Badge tone="ok">ready</Badge> : <Badge tone="bad">failed</Badge>}
                      <Button variant="ghost" size="icon" aria-label="Remove repository" onClick={() => remove(r.id)}><Trash2 className="h-3.5 w-3.5" /></Button>
                    </div>
                  </div>
                  {r.status === "failed" && <p className="rounded border border-bad/30 bg-bad/5 px-2 py-1.5 text-xs text-bad">{r.error}</p>}
                  {r.status === "ready" && (
                    <dl className="rounded border border-line bg-raised/40 px-3 py-1">
                      <Kv k="Files">{fmtInt(r.fileCount)}</Kv>
                      <Kv k="Size">{fmtBytes(r.sizeBytes)}</Kv>
                      {r.summary && <Kv k="Languages">{Object.entries(r.summary.extensions).slice(0, 5).map(([e, n]) => `${e} ${n}`).join(" · ")}</Kv>}
                    </dl>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Dialog open={ghOpen} onOpenChange={setGhOpen} title="Connect a GitHub repository" description="Shallow-cloned on the server into an isolated snapshot. Agents never see credentials."
        footer={<><Button variant="ghost" onClick={() => setGhOpen(false)}>Cancel</Button><Button variant="primary" loading={busy} disabled={!url.trim()} onClick={importGh}>Import</Button></>}>
        <div className="space-y-4">
          <Field label="Repository URL" hint="https://github.com/owner/repo"><Input autoFocus value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://github.com/owner/repo" /></Field>
          <Field label="Branch or tag (optional)"><Input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="default branch" /></Field>
          <Field label="Access token for a private repo (optional)" hint="Used once for the clone. Never stored, logged, or exposed to agents."><Input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} /></Field>
        </div>
      </Dialog>
      <NewWorkflowDialog open={nwOpen} onOpenChange={setNwOpen} projectId={id} />
    </div>
  );
}
