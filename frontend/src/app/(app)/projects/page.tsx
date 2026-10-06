"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FolderGit2, Plus } from "lucide-react";
import { Button, Empty, ErrorState, Field, Input, PageHeader, Panel, Skeleton, Textarea, tableCls } from "@/components/ui/primitives";
import { Dialog, useToast } from "@/components/ui/overlay";
import { ApiError, api } from "@/lib/api";
import { useQuery } from "@/lib/hooks";
import { fmtRelative } from "@/lib/format";
import type { Project } from "@/lib/types";

export default function ProjectsPage() {
  const q = useQuery<{ projects: Project[] }>("/api/projects");
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [desc, setDesc] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const router = useRouter();
  const { push } = useToast();

  async function create() {
    setBusy(true);
    try {
      const p = await api.post<Project>("/api/projects", { name, description: desc });
      setOpen(false);
      setName("");
      setDesc("");
      router.push(`/projects/${p.id}`);
    } catch (e) {
      push({ tone: "bad", title: "Couldn't create the project", body: (e as ApiError).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-[1100px] p-6">
      <PageHeader title="Projects" subtitle="A project is a persistent environment: repository, workflow versions, runs, artifacts and memory." actions={<Button variant="primary" onClick={() => setOpen(true)}><Plus className="h-3.5 w-3.5" /> New project</Button>} />
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} /> : (
        <Panel flush>
          {!q.data ? <Skeleton className="m-3 h-24" /> : q.data.projects.length === 0 ? (
            <Empty icon={<FolderGit2 className="h-6 w-6" />} title="No projects yet" action={<Button variant="primary" onClick={() => setOpen(true)}>Create a project</Button>}>
              Start with a project like “Security Audit”, then connect a GitHub repository or upload a ZIP.
            </Empty>
          ) : (
            <table className={tableCls.table}>
              <thead><tr><th className={tableCls.th}>Name</th><th className={tableCls.th}>Description</th><th className={tableCls.th + " text-right"}>Created</th></tr></thead>
              <tbody>
                {q.data.projects.map((p) => (
                  <tr key={p.id} className={tableCls.tr}>
                    <td className={tableCls.td}><Link className="font-medium hover:text-ember" href={`/projects/${p.id}`}>{p.name}</Link></td>
                    <td className={tableCls.td + " max-w-[480px] truncate text-muted"}>{p.description || <span className="text-faint">—</span>}</td>
                    <td className={tableCls.td + " num text-right text-muted"}>{fmtRelative(p.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      )}
      <Dialog open={open} onOpenChange={setOpen} title="New project" footer={<><Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" onClick={create} loading={busy} disabled={!name.trim()}>Create</Button></>}>
        <div className="space-y-4">
          <Field label="Name"><Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Security Audit" maxLength={120} /></Field>
          <Field label="Description (optional)"><Textarea rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={2000} /></Field>
        </div>
      </Dialog>
    </div>
  );
}
