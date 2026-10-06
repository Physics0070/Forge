"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { GitCompare, Play } from "lucide-react";
import { RunsTable } from "@/components/runs-table";
import { Button, Empty, ErrorState, PageHeader, Panel, Select, Skeleton } from "@/components/ui/primitives";
import { useQuery } from "@/lib/hooks";
import { qs } from "@/lib/api";
import type { Run } from "@/lib/types";

const STATUSES = ["", "RUNNING", "WAITING_APPROVAL", "BLOCKED", "PAUSED", "SUCCESS", "FAILED", "CANCELLED"];

export default function RunsPage() {
  const [status, setStatus] = React.useState("");
  const [sel, setSel] = React.useState<string[]>([]);
  const router = useRouter();
  const q = useQuery<{ runs: Run[] }>(`/api/runs${qs({ status, limit: 100 })}`, { every: 6000 });
  const toggle = (id: string) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s.slice(-1), id]));
  return (
    <div className="mx-auto max-w-[1280px] p-6">
      <PageHeader title="Runs" subtitle="Every execution, with the exact workflow version, models, cost basis and outcome."
        actions={<>
          <Select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)} className="w-48">{STATUSES.map((s) => <option key={s} value={s}>{s ? s.replace("_", " ").toLowerCase() : "All statuses"}</option>)}</Select>
          <Button disabled={sel.length !== 2} onClick={() => router.push(`/compare?a=${sel[0]}&b=${sel[1]}`)}><GitCompare className="h-3.5 w-3.5" /> Compare selected ({sel.length}/2)</Button>
        </>} />
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} /> : (
        <Panel flush>
          {!q.data ? <Skeleton className="m-3 h-24" /> : q.data.runs.length === 0 ? (
            <Empty icon={<Play className="h-6 w-6" />} title={status ? "No runs with this status" : "No runs yet"}>Approve a workflow version and execute it to see it here.</Empty>
          ) : <RunsTable runs={q.data.runs} selectable selected={sel} onSelect={toggle} />}
        </Panel>
      )}
    </div>
  );
}
