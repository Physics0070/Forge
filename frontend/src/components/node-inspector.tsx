"use client";
import * as React from "react";
import { AlertTriangle, Lock, Trash2, Unlock } from "lucide-react";
import { Button, Checkbox, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { NODE_TYPE_META } from "@/components/status";
import { requiredPermissions } from "@/lib/workflow-edit";
import { cn } from "@/lib/utils";
import type { IR, IREdge, IRNode, Issue, ToolDef } from "@/lib/types";

const OPS = ["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "exists", "truthy", "len_gt", "len_gte", "len_eq"];

function Section({ title, children, defaultOpen = true }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) {
  return (
    <details open={defaultOpen} className="group border-b border-line">
      <summary className="flex h-8 cursor-pointer list-none items-center px-4 text-xs font-medium uppercase tracking-wider text-faint hover:text-muted">{title}</summary>
      <div className="space-y-3 px-4 pb-4">{children}</div>
    </details>
  );
}

function JsonField({ label, value, onChange, rows = 5, disabled }: { label: string; value: unknown; onChange: (v: any) => void; rows?: number; disabled?: boolean }) {
  const [text, setText] = React.useState(JSON.stringify(value ?? {}, null, 2));
  const [err, setErr] = React.useState<string | null>(null);
  const last = React.useRef(JSON.stringify(value ?? {}));
  React.useEffect(() => {
    const now = JSON.stringify(value ?? {});
    if (now !== last.current) { last.current = now; setText(JSON.stringify(value ?? {}, null, 2)); setErr(null); }
  }, [value]);
  return (
    <Field label={label} error={err}>
      <Textarea rows={rows} disabled={disabled} className="mono text-xs" spellCheck={false} value={text}
        onChange={(e) => {
          setText(e.target.value);
          try { const v = JSON.parse(e.target.value); setErr(null); last.current = JSON.stringify(v); onChange(v); } catch { setErr("Invalid JSON"); }
        }} />
    </Field>
  );
}

function Predicate({ value, onChange, disabled }: { value: any; onChange: (v: any) => void; disabled?: boolean }) {
  const p = value ?? {};
  const [raw, setRaw] = React.useState(JSON.stringify(p.value ?? ""));
  return (
    <div className="grid grid-cols-[1fr_92px_1fr] gap-2">
      <Input disabled={disabled} aria-label="Path" className="mono text-xs" value={p.path ?? ""} onChange={(e) => onChange({ ...p, path: e.target.value })} placeholder="$.field" />
      <Select disabled={disabled} aria-label="Operator" value={p.op ?? "eq"} onChange={(e) => onChange({ ...p, op: e.target.value })}>{OPS.map((o) => <option key={o}>{o}</option>)}</Select>
      <Input disabled={disabled} aria-label="Value" className="mono text-xs" value={raw}
        onChange={(e) => { setRaw(e.target.value); try { onChange({ ...p, value: JSON.parse(e.target.value) }); } catch { onChange({ ...p, value: e.target.value }); } }} />
    </div>
  );
}

function IdMulti({ label, options, value, onChange, disabled }: { label: string; options: string[]; value: string[]; onChange: (v: string[]) => void; disabled?: boolean }) {
  return (
    <Field label={label}>
      <div className="flex flex-wrap gap-1.5">
        {options.length === 0 && <span className="text-xs text-faint">No candidates yet. Connect nodes first.</span>}
        {options.map((o) => {
          const on = value.includes(o);
          return (
            <button key={o} type="button" disabled={disabled} onClick={() => onChange(on ? value.filter((x) => x !== o) : [...value, o])}
              className={cn("mono rounded border px-1.5 py-0.5 text-xs", on ? "border-ember/60 bg-ember/10 text-ember" : "border-line-strong text-muted hover:text-fg")}>{o}</button>
          );
        })}
      </div>
    </Field>
  );
}

export function NodeInspector({ node, ir, tools, models, issues, readOnly, onChange, onDelete }: {
  node: IRNode; ir: IR; tools: ToolDef[]; models: string[]; issues: Issue[]; readOnly: boolean;
  onChange: (patch: Partial<IRNode>) => void; onDelete: () => void;
}) {
  const M = NODE_TYPE_META[node.type];
  const cfg = node.config ?? {};
  const setCfg = (patch: Record<string, any>) => onChange({ config: { ...cfg, ...patch } });
  const upstream = ir.edges.filter((e) => e.target === node.id).map((e) => e.source);
  const others = ir.nodes.filter((n) => n.id !== node.id).map((n) => n.id);
  const needPerms = requiredPermissions(node.tools ?? [], tools);
  const missingPerms = needPerms.filter((p) => !(node.permissions ?? []).includes(p));
  const ro = readOnly || !!node.locked;
  const rp = node.retryPolicy ?? {};
  const vp = node.verificationPolicy ?? {};
  const rt = node.routing ?? {};

  return (
    <div>
      <div className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <M.icon className="h-4 w-4 shrink-0 text-ember" />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">{node.name || node.id}</p>
            <p className="mono truncate text-xs text-faint">{node.id} · {M.label}</p>
          </div>
        </div>
        {!readOnly && (
          <div className="flex shrink-0 gap-1">
            <Button variant="ghost" size="icon" aria-label={node.locked ? "Unlock node" : "Lock node"} title="Locked nodes can't be edited or deleted" onClick={() => onChange({ locked: !node.locked })}>{node.locked ? <Lock className="h-3.5 w-3.5 text-warn" /> : <Unlock className="h-3.5 w-3.5" />}</Button>
            <Button variant="ghost" size="icon" aria-label="Delete node" disabled={node.locked} onClick={onDelete}><Trash2 className="h-3.5 w-3.5" /></Button>
          </div>
        )}
      </div>
      <p className="border-b border-line px-4 py-2 text-xs text-muted">{M.hint}</p>

      {issues.length > 0 && (
        <div className="space-y-1 border-b border-line bg-bad/5 px-4 py-2.5">
          {issues.map((i, k) => (
            <p key={k} className={cn("flex gap-1.5 text-xs", i.severity === "error" ? "text-bad" : "text-warn")}><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{i.message}</p>
          ))}
        </div>
      )}

      <Section title="General">
        <Field label="Name"><Input disabled={ro} value={node.name ?? ""} onChange={(e) => onChange({ name: e.target.value })} /></Field>
        {node.type === "AGENT" && <Field label="Agent"><Input disabled value={node.agentId ?? ""} className="mono text-xs" /></Field>}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Timeout (s)"><Input disabled={ro} type="number" min={1} max={3600} value={node.timeoutS ?? 120} onChange={(e) => onChange({ timeoutS: Math.max(1, Number(e.target.value) || 1) })} /></Field>
          <Field label="Model" hint="Auto = routed per task">
            <Select disabled={ro || node.type !== "AGENT"} value={node.model ?? ""} onChange={(e) => onChange({ model: e.target.value || null })}>
              <option value="">Auto (router)</option>
              {models.map((m) => <option key={m} value={m}>{m}</option>)}
            </Select>
          </Field>
        </div>
      </Section>

      {node.type === "AGENT" && (
        <>
          <Section title="Model routing">
            <div className="grid grid-cols-3 gap-3">
              {(["complexity", "risk", "latency"] as const).map((k) => (
                <Field key={k} label={k[0].toUpperCase() + k.slice(1)}>
                  <Select disabled={ro} value={(rt as any)[k] ?? (k === "latency" ? "normal" : k === "risk" ? "low" : "medium")} onChange={(e) => onChange({ routing: { ...rt, [k]: e.target.value } })}>
                    {(k === "latency" ? ["fast", "normal", "relaxed"] : ["low", "medium", "high"]).map((o) => <option key={o}>{o}</option>)}
                  </Select>
                </Field>
              ))}
            </div>
            <Checkbox disabled={ro} checked={!!rt.verificationCritical} onChange={(v) => onChange({ routing: { ...rt, verificationCritical: v } })} label="Verification-critical (never down-routed for latency)" />
          </Section>

          <Section title="Tools & permissions">
            <div className="space-y-1.5">
              {tools.map((t) => {
                const on = (node.tools ?? []).includes(t.id);
                return (
                  <label key={t.id} className={cn("flex cursor-pointer items-start gap-2 rounded border px-2 py-1.5", on ? "border-ember/40 bg-ember/5" : "border-line", ro && "pointer-events-none opacity-60")}>
                    <input type="checkbox" className="mt-0.5 accent-[rgb(var(--ember))]" checked={on}
                      onChange={(e) => onChange({ tools: e.target.checked ? [...(node.tools ?? []), t.id] : (node.tools ?? []).filter((x) => x !== t.id) })} />
                    <span className="min-w-0">
                      <span className="flex items-center gap-1.5 text-sm"><span className="mono text-xs">{t.id}</span>{t.requiresApproval && <span className="rounded bg-warn/15 px-1 text-2xs text-warn">needs approval</span>}</span>
                      <span className="block text-xs text-faint">{t.description}</span>
                    </span>
                  </label>
                );
              })}
            </div>
            <Field label="Granted permissions" hint="A tool call is denied unless every permission it needs is granted here AND allowed by the workflow policy.">
              <div className="flex flex-wrap gap-1.5">
                {[...new Set([...(ir.policies?.allowedPermissions ?? []), ...(node.permissions ?? [])])].sort().map((p) => {
                  const on = (node.permissions ?? []).includes(p);
                  return <button key={p} type="button" disabled={ro} onClick={() => onChange({ permissions: on ? (node.permissions ?? []).filter((x) => x !== p) : [...(node.permissions ?? []), p] })}
                    className={cn("mono rounded border px-1.5 py-0.5 text-xs", on ? "border-ok/50 bg-ok/10 text-ok" : "border-line-strong text-muted hover:text-fg")}>{p}</button>;
                })}
              </div>
            </Field>
            {missingPerms.length > 0 && <p className="flex gap-1.5 text-xs text-bad"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" /> Missing permission(s) for selected tools: <span className="mono">{missingPerms.join(", ")}</span></p>}
          </Section>

          <Section title="Retry policy" defaultOpen={false}>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Max attempts"><Input disabled={ro} type="number" min={1} max={6} value={rp.maxAttempts ?? 2} onChange={(e) => onChange({ retryPolicy: { ...rp, maxAttempts: Number(e.target.value) } })} /></Field>
              <Field label="Backoff"><Select disabled={ro} value={rp.backoff ?? "exponential"} onChange={(e) => onChange({ retryPolicy: { ...rp, backoff: e.target.value } })}><option>none</option><option>fixed</option><option>exponential</option></Select></Field>
              <Field label="Base delay (s)"><Input disabled={ro} type="number" min={0} step={0.5} value={rp.baseDelayS ?? 1} onChange={(e) => onChange({ retryPolicy: { ...rp, baseDelayS: Number(e.target.value) } })} /></Field>
              <Field label="Validation retries"><Input disabled={ro} type="number" min={0} max={3} value={rp.validationRetries ?? 1} onChange={(e) => onChange({ retryPolicy: { ...rp, validationRetries: Number(e.target.value) } })} /></Field>
            </div>
            <Field label="Fallback model" hint="Used on the last attempt or after a model-quality failure.">
              <Select disabled={ro} value={rp.fallbackModel ?? ""} onChange={(e) => onChange({ retryPolicy: { ...rp, fallbackModel: e.target.value || null } })}><option value="">None (router fallback)</option>{models.map((m) => <option key={m}>{m}</option>)}</Select>
            </Field>
          </Section>

          <Section title="Verification" defaultOpen={false}>
            <Checkbox disabled={ro} checked={!!vp.required} onChange={(v) => onChange({ verificationPolicy: { ...vp, required: v } })} label="Verify this node's findings before continuing" />
            <div className="grid grid-cols-2 gap-3">
              <Field label="Min confidence"><Input disabled={ro} type="number" min={0} max={1} step={0.05} value={vp.minConfidence ?? 0.6} onChange={(e) => onChange({ verificationPolicy: { ...vp, minConfidence: Number(e.target.value) } })} /></Field>
              <Field label="On failure"><Select disabled={ro} value={vp.onFail ?? "fail"} onChange={(e) => onChange({ verificationPolicy: { ...vp, onFail: e.target.value } })}><option value="fail">fail node</option><option value="recover">hand to recovery</option><option value="continue_flagged">continue, flagged</option></Select></Field>
            </div>
          </Section>

          <Section title="Budget" defaultOpen={false}>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Max USD"><Input disabled={ro} type="number" min={0} step={0.01} value={node.budget?.maxUsd ?? ""} placeholder="none" onChange={(e) => onChange({ budget: { ...node.budget, maxUsd: e.target.value === "" ? null : Number(e.target.value) } })} /></Field>
              <Field label="Max tokens"><Input disabled={ro} type="number" min={1} value={node.budget?.maxTokens ?? ""} placeholder="none" onChange={(e) => onChange({ budget: { ...node.budget, maxTokens: e.target.value === "" ? null : Number(e.target.value) } })} /></Field>
            </div>
          </Section>
        </>
      )}

      {node.type === "CONDITION" && <Section title="Predicate"><Predicate disabled={ro} value={cfg.predicate} onChange={(v) => setCfg({ predicate: v })} /><p className="text-xs text-faint">Outgoing edges are labelled <span className="mono">true</span> / <span className="mono">false</span>; the other branch is skipped.</p></Section>}
      {node.type === "JOIN" && (
        <Section title="Join">
          <IdMulti disabled={ro} label="Required branches (waits only for these)" options={upstream} value={cfg.required ?? []} onChange={(v) => setCfg({ required: v })} />
          <Field label="Mode"><Select disabled={ro} value={cfg.mode ?? "all"} onChange={(e) => setCfg({ mode: e.target.value })}><option value="all">all required</option><option value="any">first to finish</option></Select></Field>
          <JsonField disabled={ro} label="Concat (output field → source paths)" value={cfg.concat ?? {}} onChange={(v) => setCfg({ concat: v })} rows={4} />
        </Section>
      )}
      {node.type === "RETRY" && (
        <Section title="Retry gate">
          <Field label="Target (direct upstream)"><Select disabled={ro} value={cfg.target ?? ""} onChange={(e) => setCfg({ target: e.target.value })}><option value="">Select…</option>{upstream.map((u) => <option key={u}>{u}</option>)}</Select></Field>
          <Field label="Until"><Predicate disabled={ro} value={cfg.until} onChange={(v) => setCfg({ until: v })} /></Field>
          <Field label="Max iterations"><Input disabled={ro} type="number" min={1} max={6} value={cfg.max_iterations ?? 2} onChange={(e) => setCfg({ max_iterations: Number(e.target.value) })} /></Field>
        </Section>
      )}
      {node.type === "APPROVAL" && (
        <Section title="Approval gate">
          <Field label="Kind"><Select disabled={ro} value={cfg.kind ?? "approve_action"} onChange={(e) => setCfg({ kind: e.target.value })}>
            <option value="approve_patch">Approve proposed code changes</option><option value="approve_external_side_effect">Approve external side effect</option>
            <option value="approve_high_cost">Approve high-cost execution</option><option value="approve_production_action">Approve production-impacting action</option><option value="approve_action">Approve action</option><option value="approve_final_diff">Approve final diff</option></Select></Field>
          <Field label="Title"><Input disabled={ro} value={cfg.title ?? ""} onChange={(e) => setCfg({ title: e.target.value })} /></Field>
          <Field label="Instructions for the reviewer"><Textarea disabled={ro} rows={3} value={cfg.summary ?? ""} onChange={(e) => setCfg({ summary: e.target.value })} /></Field>
          <Field label="If rejected"><Select disabled={ro} value={cfg.on_reject ?? "fail"} onChange={(e) => setCfg({ on_reject: e.target.value })}><option value="fail">fail this branch</option><option value="skip">skip dependent steps and continue</option></Select></Field>
        </Section>
      )}
      {node.type === "RECOVERY" && (
        <Section title="Recovery">
          <IdMulti disabled={ro} label="Watches (repairs these if they fail)" options={others} value={cfg.watches ?? []} onChange={(v) => setCfg({ watches: v })} />
          <JsonField disabled={ro} label="Fallback output (used as the failed node's output)" value={cfg.fallback_output ?? {}} onChange={(v) => setCfg({ fallback_output: v })} />
          <JsonField disabled={ro} label="Per-node fallback outputs (optional)" value={cfg.fallback_outputs ?? {}} onChange={(v) => setCfg({ fallback_outputs: v })} rows={4} />
        </Section>
      )}
      {node.type === "TRANSFORM" && (
        <Section title="Transform">
          <JsonField disabled={ro} label="select (target → $.path)" value={cfg.select ?? {}} onChange={(v) => setCfg({ select: v })} rows={4} />
          <JsonField disabled={ro} label="set (literal | {from} | {concat:[…]} | {count})" value={cfg.set ?? {}} onChange={(v) => setCfg({ set: v })} rows={5} />
        </Section>
      )}
      {node.type === "VERIFICATION" && (
        <Section title="Verification">
          <Field label="Findings path"><Input disabled={ro} className="mono text-xs" value={cfg.findings_path ?? "$.findings"} onChange={(e) => setCfg({ findings_path: e.target.value })} /></Field>
          <p className="text-xs text-faint">Checks each claim against the cited file/line, the quoted evidence, independent rule re-checks, the agent's tool trace and external evidence. A second model is one bounded signal; it can never verify on its own.</p>
        </Section>
      )}

      <Section title="Typed contract" defaultOpen={false}>
        <JsonField disabled={ro} label="Input schema" value={node.inputSchema ?? { type: "object" }} onChange={(v) => onChange({ inputSchema: v })} rows={6} />
        <JsonField disabled={ro} label="Output schema" value={node.outputSchema ?? { type: "object" }} onChange={(v) => onChange({ outputSchema: v })} rows={6} />
      </Section>
    </div>
  );
}

export function EdgeInspector({ edge, ir, readOnly, onChange, onDelete }: { edge: IREdge; ir: IR; readOnly: boolean; onChange: (p: Partial<IREdge>) => void; onDelete: () => void }) {
  const src = ir.nodes.find((n) => n.id === edge.source);
  const dst = ir.nodes.find((n) => n.id === edge.target);
  const mapping = edge.mapping ?? [];
  return (
    <div>
      <div className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold">Typed handoff</p>
          <p className="mono truncate text-xs text-faint">{edge.source} → {edge.target}</p>
        </div>
        {!readOnly && <Button variant="ghost" size="icon" aria-label="Delete edge" onClick={onDelete}><Trash2 className="h-3.5 w-3.5" /></Button>}
      </div>
      <div className="space-y-3 p-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Kind"><Select disabled={readOnly} value={edge.kind ?? "data"} onChange={(e) => onChange({ kind: e.target.value as IREdge["kind"] })}><option value="data">data</option><option value="control">control</option><option value="failure">failure</option></Select></Field>
          <Field label="Condition label"><Select disabled={readOnly || src?.type !== "CONDITION"} value={edge.condition ?? ""} onChange={(e) => onChange({ condition: e.target.value || null })}><option value="">—</option><option>true</option><option>false</option></Select></Field>
        </div>
        <Field label="If the handoff fails validation">
          <Select disabled={readOnly} value={edge.onHandoffFailure ?? "retry_source"} onChange={(e) => onChange({ onHandoffFailure: e.target.value })}>
            <option value="retry_source">re-run the source (bounded)</option><option value="fail">fail the target</option><option value="recover">fail, let recovery handle it</option>
          </Select>
        </Field>
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-xs font-medium text-muted">Field mapping</span>
            {!readOnly && <Button size="xs" onClick={() => onChange({ mapping: [...mapping, { from: "$.", to: "" }] })}>Add</Button>}
          </div>
          {mapping.length === 0 && <p className="text-xs text-faint">No mapping: the whole output of <span className="mono">{src?.id}</span> becomes the input of <span className="mono">{dst?.id}</span> (still schema-validated).</p>}
          <div className="space-y-1.5">
            {mapping.map((m, i) => (
              <div key={i} className="grid grid-cols-[1fr_16px_1fr_24px] items-center gap-1.5">
                <Input disabled={readOnly} className="mono text-xs" value={m.from} onChange={(e) => onChange({ mapping: mapping.map((x, j) => (j === i ? { ...x, from: e.target.value } : x)) })} aria-label="From path" />
                <span className="text-center text-faint">→</span>
                <Input disabled={readOnly} className="mono text-xs" value={m.to} onChange={(e) => onChange({ mapping: mapping.map((x, j) => (j === i ? { ...x, to: e.target.value } : x)) })} aria-label="To field" />
                {!readOnly && <button className="text-faint hover:text-bad" aria-label="Remove mapping" onClick={() => onChange({ mapping: mapping.filter((_, j) => j !== i) })}><Trash2 className="h-3 w-3" /></button>}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
