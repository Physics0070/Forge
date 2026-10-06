"use client";
import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, X, XCircle, Info } from "lucide-react";
import { cn } from "@/lib/utils";

/* ── Dialog ─────────────────────────────────────────────────────────────── */
export function Dialog({ open, onOpenChange, title, description, children, width = "max-w-lg", footer }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: string; description?: string; children: React.ReactNode; width?: string; footer?: React.ReactNode;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-[1px] data-[state=open]:animate-rise" />
        <DialogPrimitive.Content
          className={cn("fixed left-1/2 top-[8vh] z-50 max-h-[84vh] w-[calc(100vw-32px)] -translate-x-1/2 overflow-hidden rounded-lg border border-line-strong bg-panel shadow-2xl data-[state=open]:animate-rise", width)}
        >
          <div className="flex items-start justify-between gap-4 border-b border-line px-4 py-3">
            <div>
              <DialogPrimitive.Title className="text-sm font-semibold">{title}</DialogPrimitive.Title>
              <DialogPrimitive.Description className={cn("mt-0.5 text-xs text-faint", !description && "sr-only")}>{description ?? title}</DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close className="rounded p-1 text-faint hover:bg-hover hover:text-fg" aria-label="Close">
              <X className="h-4 w-4" />
            </DialogPrimitive.Close>
          </div>
          <div className="max-h-[62vh] overflow-y-auto px-4 py-4">{children}</div>
          {footer && <div className="flex items-center justify-end gap-2 border-t border-line bg-raised/50 px-4 py-2.5">{footer}</div>}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** Right-hand drawer (node inspector, artifact inspector). */
export function Drawer({ open, onClose, title, subtitle, children, width = "w-[560px]" }: {
  open: boolean; onClose: () => void; title: React.ReactNode; subtitle?: React.ReactNode; children: React.ReactNode; width?: string;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <DialogPrimitive.Content
          className={cn("fixed bottom-0 right-0 top-0 z-50 flex max-w-full flex-col border-l border-line-strong bg-panel shadow-2xl", width)}
          style={{ animation: "rise 160ms ease-out both" }}
        >
          <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
            <div className="min-w-0">
              <DialogPrimitive.Title className="truncate text-sm font-semibold">{title}</DialogPrimitive.Title>
              <DialogPrimitive.Description className={cn("truncate text-xs text-faint", !subtitle && "sr-only")}>{subtitle ?? "Details"}</DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close className="rounded p-1 text-faint hover:bg-hover hover:text-fg" aria-label="Close">
              <X className="h-4 w-4" />
            </DialogPrimitive.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/* ── Tooltip ─────────────────────────────────────────────────────────────── */
export function Tip({ content, children, side = "top" }: { content: React.ReactNode; children: React.ReactNode; side?: "top" | "bottom" | "left" | "right" }) {
  if (!content) return <>{children}</>;
  return (
    <TooltipPrimitive.Provider delayDuration={250}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content side={side} sideOffset={6} className="z-[60] max-w-xs rounded border border-line-strong bg-raised px-2 py-1.5 text-xs text-fg shadow-lg">
            {content}
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}

/* ── Tabs ────────────────────────────────────────────────────────────────── */
export function Tabs<T extends string>({ tabs, value, onChange, className }: { tabs: { id: T; label: React.ReactNode; count?: number | null }[]; value: T; onChange: (v: T) => void; className?: string }) {
  return (
    <div role="tablist" className={cn("flex items-center gap-0.5 border-b border-line", className)}>
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            "relative -mb-px h-8 px-3 text-sm transition-colors",
            value === t.id ? "text-fg" : "text-faint hover:text-muted",
          )}
        >
          <span className="inline-flex items-center gap-1.5">
            {t.label}
            {t.count !== undefined && t.count !== null && <span className="num rounded bg-raised px-1 text-2xs text-muted">{t.count}</span>}
          </span>
          {value === t.id && <span className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-ember" />}
        </button>
      ))}
    </div>
  );
}

/* ── Toasts ──────────────────────────────────────────────────────────────── */
interface ToastItem { id: number; tone: "ok" | "bad" | "info"; title: string; body?: string }
const ToastCtx = React.createContext<{ push: (t: Omit<ToastItem, "id">) => void }>({ push: () => {} });
export const useToast = () => React.useContext(ToastCtx);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = React.useState<ToastItem[]>([]);
  const push = React.useCallback((t: Omit<ToastItem, "id">) => {
    const id = Date.now() + Math.random();
    setItems((s) => [...s.slice(-3), { ...t, id }]);
    setTimeout(() => setItems((s) => s.filter((x) => x.id !== id)), t.tone === "bad" ? 7000 : 3800);
  }, []);
  return (
    <ToastCtx.Provider value={{ push }}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[70] flex w-80 flex-col gap-2" aria-live="polite">
        <AnimatePresence>
          {items.map((t) => (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="pointer-events-auto flex items-start gap-2.5 rounded-md border border-line-strong bg-raised p-3 shadow-xl"
            >
              {t.tone === "ok" ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-ok" /> : t.tone === "bad" ? <XCircle className="mt-0.5 h-4 w-4 text-bad" /> : <Info className="mt-0.5 h-4 w-4 text-info" />}
              <div className="min-w-0">
                <p className="text-sm font-medium">{t.title}</p>
                {t.body && <p className="mt-0.5 text-xs text-muted">{t.body}</p>}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastCtx.Provider>
  );
}
