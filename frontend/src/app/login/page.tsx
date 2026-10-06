"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { Wordmark } from "@/components/brand";
import { Button, Field, Input } from "@/components/ui/primitives";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";

const STAGES = [
  ["Goal", "Describe the outcome in plain language."],
  ["Compile", "Nemotron decomposes it into a typed DAG. Invalid graphs are never emitted."],
  ["Inspect & edit", "See every node, model, tool, permission and budget before anything runs."],
  ["Approve", "Immutable version. Writes need a human gate."],
  ["Execute", "Durable workers, parallel branches, checkpoints, budgets that actually stop spend."],
  ["Verify", "Claims are checked against the code and evidence, not against another prompt."],
  ["Replay", "Same input, any model or version. Compare runs side by side."],
];

export default function LoginPage() {
  const { me, loading, login, signup } = useAuth();
  const router = useRouter();
  const [mode, setMode] = React.useState<"in" | "up">("in");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [ws, setWs] = React.useState("");
  const [err, setErr] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (!loading && me) router.replace("/dashboard");
  }, [loading, me, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      if (mode === "in") await login(email, password);
      else await signup(email, password, ws.trim() || "My Workspace");
      router.replace("/dashboard");
    } catch (e) {
      const a = e as ApiError;
      setErr(a.status === 422 ? "Use a valid email and a password of at least 10 characters." : a.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen bg-bg lg:grid-cols-[minmax(0,1.15fr)_minmax(420px,0.85fr)]">
      <section className="relative hidden flex-col justify-between overflow-hidden border-r border-line bg-panel p-12 lg:flex">
        <Wordmark />
        <div className="max-w-xl">
          <h1 className="text-[40px] font-semibold leading-[1.05] tracking-tight">
            Don&apos;t trust an agent.
            <br />
            <span className="text-ember">Verify the workflow.</span>
          </h1>
          <p className="mt-5 max-w-lg text-[15px] leading-6 text-muted">
            FORGE turns a goal into a typed, observable, permission-controlled workflow, then routes models, enforces budgets, validates every handoff and checks the results against evidence.
          </p>
          <ol className="mt-10 space-y-0">
            {STAGES.map(([t, d], i) => (
              <li key={t} className="relative flex gap-4 pb-5 last:pb-0">
                {i < STAGES.length - 1 && <span className="absolute left-[5px] top-4 h-full w-px bg-line-strong" />}
                <span className="relative mt-1 h-[11px] w-[11px] shrink-0 rounded-full border-2 border-ember bg-panel" />
                <div>
                  <p className="mono text-xs font-medium uppercase tracking-wider text-fg">{t}</p>
                  <p className="text-sm text-faint">{d}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
        <p className="text-xs text-faint">Inference on Nebius Token Factory · NVIDIA Nemotron open models</p>
      </section>

      <section className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-[360px] space-y-4" aria-labelledby="auth-h">
          <div className="lg:hidden"><Wordmark /></div>
          <div>
            <h2 id="auth-h" className="text-lg font-semibold">{mode === "in" ? "Sign in" : "Create your workspace"}</h2>
            <p className="mt-0.5 text-sm text-muted">{mode === "in" ? "Welcome back." : "Workspaces are isolated: nobody else can see your projects, runs or artifacts."}</p>
          </div>
          {mode === "up" && <Field label="Workspace name"><Input value={ws} onChange={(e) => setWs(e.target.value)} placeholder="Acme Security" maxLength={120} /></Field>}
          <Field label="Email"><Input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" /></Field>
          <Field label="Password" hint={mode === "up" ? "At least 10 characters." : undefined}>
            <Input type="password" autoComplete={mode === "in" ? "current-password" : "new-password"} required minLength={mode === "up" ? 10 : 1} value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          {err && <p role="alert" className="rounded border border-bad/30 bg-bad/5 px-3 py-2 text-sm text-bad">{err}</p>}
          <Button type="submit" variant="primary" size="md" className="w-full" loading={busy}>{mode === "in" ? "Sign in" : "Create account"}</Button>
          <p className="text-center text-sm text-muted">
            {mode === "in" ? "New to FORGE?" : "Already have an account?"}{" "}
            <button type="button" className="text-ember hover:underline" onClick={() => { setMode(mode === "in" ? "up" : "in"); setErr(null); }}>
              {mode === "in" ? "Create a workspace" : "Sign in"}
            </button>
          </p>
        </form>
      </section>
    </div>
  );
}
