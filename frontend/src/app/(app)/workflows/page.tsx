"use client";
import Link from "next/link";
import { Workflow as WorkflowIcon } from "lucide-react";
import { WorkflowsTable } from "@/components/workflows-table";
import { Button, Empty, ErrorState, PageHeader, Panel, Skeleton } from "@/components/ui/primitives";
import { useQuery } from "@/lib/hooks";
import type { Workflow } from "@/lib/types";

export default function AllWorkflowsPage() {
  const q = useQuery<{ workflows: Workflow[] }>("/api/workflows");
  return (
    <div className="mx-auto max-w-[1100px] p-6">
      <PageHeader title="Workflows" subtitle="Every workflow in this workspace. Versions are immutable; runs always reference the exact approved version." />
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} /> : (
        <Panel flush>
          {!q.data ? <Skeleton className="m-3 h-24" /> : (
            <WorkflowsTable workflows={q.data.workflows.filter((w) => w.templateKey !== "eval")} empty={
              <Empty icon={<WorkflowIcon className="h-6 w-6" />} title="No workflows yet" action={<Link href="/projects"><Button variant="primary">Go to projects</Button></Link>}>Workflows are created inside a project.</Empty>} />
          )}
        </Panel>
      )}
    </div>
  );
}
