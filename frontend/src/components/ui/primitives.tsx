"use client";
import * as React from "react";
import { AlertTriangle, Check, ChevronDown, ChevronRight, Copy, Loader2 } from "lucide-react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import { ApiError } from "@/lib/api";

/* ── Button ─────────────────────────────────────────────────────────────── */
const button = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded font-medium transition-colors disabled:pointer-events-none disabled:opacity-45 select-none",
  {
    variants: {
      variant: {
        primary: "bg-ember text-ember-fg hover:bg-ember/90",
        secondary: "border border-line-strong bg-raised text-fg hover:bg-hover",
        ghost: "text-muted hover:bg-hover hover:text-fg",
        danger: "border border-bad/40 bg-bad/10 text-bad hover:bg-bad/20",
        ok: "border border-ok/40 bg-ok/10 text-ok hover:bg-ok/20",
      },
      size: { xs: "h-6 px-2 text-xs", sm: "h-7 px-2.5 text-sm", md: "h-8 px-3 text-sm", icon: "h-7 w-7" },
    },
    defaultVariants: { variant: "secondary", size: "sm" },
  },
);
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof button> {
  loading?: boolean;
}
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, loading, children, disabled, ...p },
  ref,
) {
  return (
    <button ref={ref} className={cn(button({ variant, size }), className)} disabled={disabled || loading} {...p}>
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {children}
    </button>
  );
});

/* ── Form controls ───────────────────────────────────────────────────────── */
const control =
  "w-full rounded border border-line-strong bg-bg px-2.5 text-sm text-fg placeholder:text-faint transition-colors hover:border-faint focus:border-ember focus:outline-none disabled:opacity-50";
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...p },
  ref,
) {
  return <input ref={ref} className={cn(control, "h-8", className)} {...p} />;
});
export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...p },
  ref,
) {
  return <textarea ref={ref} className={cn(control, "min-h-[72px] py-2 leading-5", className)} {...p} />;
});
export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...p },
  ref,
) {
  return (
    <div className="relative">
      <select ref={ref} className={cn(control, "h-8 appearance-none pr-7", className)} {...p}>
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2 top-2.5 h-3 w-3 text-faint" />
    </div>
  );
});
export function Field({ label, hint, error, children, className }: { label: string; hint?: React.ReactNode; error?: string | null; children: React.ReactNode; className?: string }) {
  return (
    <label className={cn("block space-y-1", className)}>
      <span className="block text-xs font-medium text-muted">{label}</span>
      {children}
      {error ? <span className="block text-xs text-bad">{error}</span> : hint ? <span className="block text-xs text-faint">{hint}</span> : null}
    </label>
  );
}
export function Checkbox({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: React.ReactNode; disabled?: boolean }) {
  return (
    <label className={cn("flex cursor-pointer items-center gap-2 text-sm text-fg", disabled && "opacity-50")}>
      <input type="checkbox" className="h-3.5 w-3.5 accent-[rgb(var(--ember))]" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

/* ── Badge / dot ─────────────────────────────────────────────────────────── */
export type Tone = "neutral" | "ok" | "warn" | "bad" | "info" | "ember";
const tones: Record<Tone, string> = {
  neutral: "border-line-strong text-muted bg-raised",
  ok: "border-ok/35 text-ok bg-ok/10",
  warn: "border-warn/35 text-warn bg-warn/10",
  bad: "border-bad/35 text-bad bg-bad/10",
  info: "border-info/35 text-info bg-info/10",
  ember: "border-ember/40 text-ember bg-ember/10",
};
export function Badge({ tone = "neutral", children, className, title }: { tone?: Tone; children: React.ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cn("inline-flex h-[18px] shrink-0 items-center gap-1 whitespace-nowrap rounded-sm border px-1.5 text-2xs font-medium uppercase tracking-wide", tones[tone], className)}>
      {children}
    </span>
  );
}
export function Dot({ tone = "neutral", pulse }: { tone?: Tone; pulse?: boolean }) {
  const c: Record<Tone, string> = { neutral: "bg-faint", ok: "bg-ok", warn: "bg-warn", bad: "bg-bad", info: "bg-info", ember: "bg-ember" };
  return <span className={cn("inline-block h-1.5 w-1.5 rounded-full", c[tone], pulse && "animate-pulseRing")} />;
}

/* ── Panel ───────────────────────────────────────────────────────────────── */
export function Panel({ title, subtitle, actions, children, className, bodyClassName, flush }: {
  title?: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode; className?: string; bodyClassName?: string; flush?: boolean;
}) {
  return (
    <section className={cn("min-w-0 rounded-md border border-line bg-panel", className)}>
      {(title || actions) && (
        <header className="flex min-h-[38px] items-center justify-between gap-3 border-b border-line px-3.5 py-2">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-fg">{title}</h2>
            {subtitle && <p className="truncate text-xs text-faint">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={cn(!flush && "p-3.5", bodyClassName)}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions, crumbs }: { title: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode; crumbs?: React.ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        {crumbs && <div className="mb-1 text-xs text-faint">{crumbs}</div>}
        <h1 className="truncate text-[18px] font-semibold leading-6 tracking-tight">{title}</h1>
        {subtitle && <p className="mt-0.5 max-w-3xl text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

/* ── Feedback ────────────────────────────────────────────────────────────── */
export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin text-faint", className)} aria-label="Loading" />;
}
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-shimmer rounded bg-[linear-gradient(90deg,rgb(var(--raised))_25%,rgb(var(--hover))_50%,rgb(var(--raised))_75%)] bg-[length:200%_100%]", className)} />;
}
export function Empty({ title, children, action, icon }: { title: string; children?: React.ReactNode; action?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {icon && <div className="mb-3 text-faint">{icon}</div>}
      <p className="text-sm font-medium text-fg">{title}</p>
      {children && <p className="mt-1 max-w-sm text-sm text-muted">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Friendly error first; raw infrastructure details are one click away, never shown by default. */
export function ErrorState({ error, title = "Something went wrong", onRetry }: { error: unknown; title?: string; onRetry?: () => void }) {
  const [open, setOpen] = React.useState(false);
  const e = error as ApiError;
  const msg = e instanceof ApiError ? e.message : "An unexpected error occurred.";
  return (
    <div className="rounded-md border border-bad/30 bg-bad/5 p-4">
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-bad" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-fg">{title}</p>
          <p className="mt-0.5 text-sm text-muted">{msg}</p>
          <div className="mt-2 flex items-center gap-3">
            {onRetry && <Button size="xs" onClick={onRetry}>Retry</Button>}
            <button className="inline-flex items-center gap-1 text-xs text-faint hover:text-muted" onClick={() => setOpen(!open)}>
              {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} View technical details
            </button>
          </div>
          {open && (
            <pre className="mono mt-2 max-h-40 overflow-auto rounded border border-line bg-bg p-2 text-xs text-muted">
              {JSON.stringify(e instanceof ApiError ? { status: e.status, code: e.code, body: e.body } : String(error), null, 2)}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

export function CopyButton({ text, label }: { text: string; label?: string }) {
  const [ok, setOk] = React.useState(false);
  return (
    <button
      className="inline-flex items-center gap-1 rounded px-1 text-faint hover:text-fg"
      aria-label="Copy"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setOk(true);
          setTimeout(() => setOk(false), 1200);
        } catch {}
      }}
    >
      {ok ? <Check className="h-3 w-3 text-ok" /> : <Copy className="h-3 w-3" />}
      {label && <span className="text-xs">{label}</span>}
    </button>
  );
}

export function Kv({ k, children, mono }: { k: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <dt className="shrink-0 text-xs text-faint">{k}</dt>
      <dd className={cn("min-w-0 truncate text-right text-sm text-fg", mono && "mono text-xs")}>{children}</dd>
    </div>
  );
}

export function Meter({ value, max, tone = "ember", label }: { value: number; max: number; tone?: Tone; label?: string }) {
  const pct = Math.max(0, Math.min(100, max > 0 ? (value / max) * 100 : 0));
  const col = pct > 90 ? "bg-bad" : pct > 70 ? "bg-warn" : tone === "ok" ? "bg-ok" : "bg-ember";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised" role="progressbar" aria-valuenow={Math.round(pct)} aria-label={label}>
      <div className={cn("h-full rounded-full transition-all", col)} style={{ width: `${pct}%` }} />
    </div>
  );
}

export const tableCls = {
  table: "w-full border-collapse text-sm",
  th: "h-8 border-b border-line px-3 text-left text-xs font-medium text-faint whitespace-nowrap",
  td: "h-9 border-b border-line/70 px-3 align-middle",
  tr: "transition-colors hover:bg-hover/60",
};
