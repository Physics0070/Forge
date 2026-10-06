// Shapes returned by the FORGE API (camelCase). Fields that can legitimately be missing are `| null`:
// the UI renders null as "Not available" and never substitutes a made-up value.

export type RunStatus = "PENDING" | "RUNNING" | "PAUSED" | "WAITING_APPROVAL" | "BLOCKED" | "SUCCESS" | "FAILED" | "CANCELLED";
export type NodeState =
  | "PENDING" | "READY" | "RUNNING" | "WAITING_APPROVAL" | "SUCCESS" | "FAILED" | "RETRYING" | "BLOCKED"
  | "CANCELLED" | "VERIFICATION_FAILED" | "RECOVERING" | "SKIPPED";
export type NodeType = "AGENT" | "PARALLEL" | "JOIN" | "CONDITION" | "RETRY" | "APPROVAL" | "VERIFICATION" | "RECOVERY" | "TRANSFORM";
export type CostBasis = "ACTUAL" | "ESTIMATED" | "UNAVAILABLE";
export type VStatus = "VERIFIED" | "REJECTED" | "UNCERTAIN";

export interface Me {
  user: { id: string; email: string };
  workspaces: { id: string; name: string; role: string }[];
  csrf_token: string;
  active_workspace_id?: string;
}

export interface Project { id: string; name: string; description: string; createdAt: string }

export interface Repository {
  id: string; projectId: string; kind: "github" | "zip"; url: string | null; ref: string | null;
  status: "importing" | "ready" | "failed"; error: string | null; commitSha: string | null;
  fileCount: number | null; sizeBytes: number | null; createdAt: string;
  summary: { extensions: Record<string, number>; top_level: Record<string, number> } | null;
}

export interface Issue { severity: "error" | "warning"; code: string; message: string; node_id?: string; edge_id?: string }

export interface IRNode {
  id: string; type: NodeType; name?: string; agentId?: string; config?: Record<string, any>;
  inputSchema?: Record<string, any>; outputSchema?: Record<string, any>; model?: string | null;
  routing?: { complexity?: string; risk?: string; latency?: string; verificationCritical?: boolean };
  tools?: string[]; permissions?: string[];
  retryPolicy?: { maxAttempts?: number; backoff?: string; baseDelayS?: number; maxDelayS?: number; jitter?: number; retryableErrors?: string[]; validationRetries?: number; fallbackModel?: string | null };
  timeoutS?: number; budget?: { maxUsd?: number | null; maxTokens?: number | null };
  verificationPolicy?: { required?: boolean; minConfidence?: number; minEvidenceScore?: number; onFail?: string };
  locked?: boolean;
}
export interface IREdge {
  id: string; source: string; target: string; kind?: "data" | "control" | "failure"; condition?: string | null;
  mapping?: { from: string; to: string; const?: any }[]; onHandoffFailure?: string;
}
export interface IR {
  irVersion?: string; id?: string; projectId?: string; version?: number; name: string; description?: string; goal: string;
  nodes: IRNode[]; edges: IREdge[]; policies?: Record<string, any>; memoryPolicy?: Record<string, any>;
  budgetPolicy?: { maxUsd?: number | null; maxTokens?: number | null; unpricedBehavior?: string };
  verificationPolicy?: Record<string, any>; outputs?: string[];
}

export interface WorkflowVersion {
  id: string; version: number; irHash: string; approved: boolean; approvedAt: string | null; createdAt: string;
  nodeCount: number; edgeCount: number; compilerMeta: Record<string, any> | null; ir?: IR; issues?: Issue[];
}
export interface Workflow {
  id: string; projectId: string; name: string; description: string; templateKey: string | null; createdAt: string;
  latestVersion: WorkflowVersion | null; versions: WorkflowVersion[];
}

export interface RunNode {
  id: string; type: NodeType | null; name: string; agentId: string | null; state: NodeState; attempt: number; iteration: number;
  model: string | null; routing: { tier?: string; model?: string; reasons?: string[]; fallbackModel?: string | null; score?: number } | null;
  error: NodeError | null; blockedReason: string | null; startedAt: string | null; finishedAt: string | null;
  reusedFromRun: string | null; feedback: any; input?: any; output?: any;
}
export interface NodeError {
  class: string; code?: string; message: string; cause?: string; detail?: Record<string, any>; attempt?: number;
  summary?: any; retry_decision?: string;
}
export interface Approval {
  id: string; runId: string; nodeId: string; kind: string; status: "PENDING" | "GRANTED" | "REJECTED";
  request: { title?: string; summary?: string; kind?: string; payload?: any }; createdAt: string; decidedAt: string | null; note: string | null;
}
export interface Totals {
  modelCalls: number; failedModelCalls: number; inputTokens: number; outputTokens: number; totalTokens: number;
  costUsd: number | null; costBasis: CostBasis; costPartial: boolean; unpricedCalls: number;
  latencyMs: { avg: number | null; p50: number | null; p95: number | null; sum: number | null };
  byModel: { model: string; calls: number; inputTokens: number; outputTokens: number; costUsd: number | null; errors: number }[];
  retries: number; policyViolations: number;
}
export interface Run {
  id: string; projectId: string; workflowId: string | null; workflowName: string; workflowVersionId: string; version: number | null;
  goal: string; status: RunStatus; input: Record<string, any>; repositoryId: string | null; parentRunId: string | null;
  replayConfig: Record<string, any> | null; createdAt: string; startedAt: string | null; completedAt: string | null;
  durationS: number | null;
  budget: { maxUsd: number | null; maxTokens: number | null; spentUsd: number; spentTokens: number; reservedUsd: number };
  totals: Totals; error: Record<string, any> | null; pendingApprovals: Approval[];
  pauseRequested: boolean; cancelRequested: boolean;
  nodes?: RunNode[]; graph?: { nodes: { id: string; type: NodeType; name: string; agentId: string | null }[]; edges: { id: string; source: string; target: string; kind: string; condition: string | null }[] };
  idempotentReplay?: boolean;
}
export interface ForgeEvent {
  id: number; runId: string | null; nodeId: string | null; type: string; status: string | null; ts: string; metadata: Record<string, any>;
}
export interface ArtifactMeta {
  id: string; runId: string | null; nodeId: string | null; type: string; schema: string; contentType: string; sizeBytes: number;
  contentHash: string; provenance: Record<string, any>; createdAt: string; content?: any; verification?: { findingId: string; status: VStatus; confidence: number; reason: string }[];
}
export interface Finding {
  id: string; title: string; severity: "critical" | "high" | "medium" | "low" | "info"; category: string; file: string; line?: number | null;
  description: string; evidence: string; confidence: number; remediation: string; references?: string[]; priority?: number | null;
  package?: string | null; version?: string | null;
}
export interface VerificationRow {
  findingId: string; status: VStatus; confidence: number; evidenceScore: number; reason: string; missingEvidence: string[];
  recommendations: string[]; checks: Record<string, any>; finding: Finding | null; artifactId: string;
}
export interface Agent {
  id: string; name: string; role: string; description: string; provider: string; model: string | null; tools: string[]; permissions: string[];
  inputSchema: any; outputSchema: any; timeoutS: number; builtin: boolean; systemContract: string;
  routing: { complexity: string; risk: string; latency: string; verificationCritical: boolean };
}
export interface ToolDef { id: string; name: string; description: string; permissions: string[]; sideEffects: string; requiresApproval: boolean }
export interface Metrics {
  runId: string; status: RunStatus; success: boolean | null; durationS: number | null; totalTokens: number; costUsd: number | null; costBasis: CostBasis;
  modelCalls: number; retries: number; failures: number; policyViolations: number; schemaValidity: number | null; verificationRate: number | null;
  verification: { verified: number; rejected: number; uncertain: number }; recoveryRate: number | null;
  duplicateWork: { duplicateToolCalls: number; totalToolCalls: number } | null; artifacts: number; nodesSucceeded: number; nodesTotal: number;
}
