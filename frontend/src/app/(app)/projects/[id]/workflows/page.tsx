"use client";
import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Plus, Workflow as WorkflowIcon } from "lucide-react";
import { NewWorkflowDialog } from "@/components/new-workflow";
import { WorkflowsTable } from "@/components/workflows-table";
import { Button, Empty, ErrorState, PageHeader, Panel, Skeleton } from "@/components/ui/primitives";
import { useQuery } from "@/lib/hooks";
import type { Project, Workflow } from "@/lib/types";

export default function ProjectWorkflowsPage() {
  const { id } = useParams<{ id: string }>();
  const project = useQuery<Project>(`/api/projects/${id}`);
  const q = useQuery<{ workflows: Workflow[] }>(`/api/workflows?project_id=${id}`);
  const [open, setOpen] = React.useState(false);
  return (
    <div className="mx-auto max-w-[1100px] p-6">
      <PageHeader crumbs={<><Link href="/projects" className="hover:text-fg">Projects</Link> / <Link href={`/projects/${id}`} className="hover:text-fg">{project.data?.name ?? "…"}</Link></>}
        title="Workflows" subtitle="Compile an objective into a typed graph, inspect it, edit it, approve it, run it."
        actions={<Button variant="primary" onClick={() => setOpen(true)}><Plus className="h-3.5 w-3.5" /> New workflow</Button>} />
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} /> : (
        <Panel flush>
          {!q.data ? <Skeleton className="m-3 h-24" /> : (
            <WorkflowsTable workflows={q.data.workflows.filter((w) => w.templateKey !== "eval")} empty={
              <Empty icon={<WorkflowIcon className="h-6 w-6" />} title="No workflows in this project" action={<Button variant="primary" onClick={() => setOpen(true)}>Create one</Button>}>
                Generate a workflow from an objective, or start from the security-audit template.</Empty>} />
          )}
        </Panel>
      )}
      <NewWorkflowDialog open={open} onOpenChange={setOpen} projectId={id} />
    </div>
  );
}
