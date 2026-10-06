"use client";
import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity, Bot, FolderGit2, GaugeCircle, GitCompare, Hand, LogOut, Moon, Play, Settings, Sun, Workflow, FlaskConical, Package, Menu, X,
} from "lucide-react";
import { Wordmark } from "@/components/brand";
import { useAuth } from "@/lib/auth";
import { useQuery } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import { Spinner, Select } from "@/components/ui/primitives";

const NAV: { group: string; items: { href: string; label: string; icon: React.ComponentType<{ className?: string }>; badge?: "approvals" }[] }[] = [
  { group: "Overview", items: [{ href: "/dashboard", label: "Dashboard", icon: GaugeCircle }] },
  { group: "Build", items: [
    { href: "/projects", label: "Projects", icon: FolderGit2 },
    { href: "/workflows", label: "Workflows", icon: Workflow },
    { href: "/agents", label: "Agents & tools", icon: Bot },
  ] },
  { group: "Execute", items: [
    { href: "/runs", label: "Runs", icon: Play, badge: "approvals" },
    { href: "/compare", label: "Compare", icon: GitCompare },
    { href: "/evaluations", label: "Evaluations", icon: FlaskConical },
  ] },
  { group: "Data", items: [{ href: "/artifacts", label: "Artifacts", icon: Package }] },
  { group: "Platform", items: [
    { href: "/system", label: "System health", icon: Activity },
    { href: "/settings", label: "Settings", icon: Settings },
  ] },
];

function useTheme() {
  const [theme, setTheme] = React.useState<"dark" | "light">("dark");
  React.useEffect(() => {
    setTheme(document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark");
  }, []);
  const toggle = () => {
    const n = theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", n);
    try { localStorage.setItem("forge.theme", n); } catch {}
    setTheme(n);
  };
  return { theme, toggle };
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { me, loading, workspace, workspaceId, switchWorkspace, logout } = useAuth();
  const path = usePathname();
  const { theme, toggle } = useTheme();
  const [open, setOpen] = React.useState(false);
  const approvals = useQuery<{ approvals: unknown[] }>(me ? "/api/approvals" : null, { every: 15000 });
  const pending = approvals.data?.approvals.length ?? 0;
  React.useEffect(() => setOpen(false), [path]);

  if (loading || !me) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg">
        <Spinner className="h-5 w-5" />
      </div>
    );
  }

  const sidebar = (
    <nav className="flex h-full w-[212px] flex-col border-r border-line bg-panel" aria-label="Primary">
      <div className="flex h-12 items-center px-4"><Link href="/dashboard"><Wordmark /></Link></div>
      <div className="px-3 pb-2">
        <label className="sr-only" htmlFor="ws">Workspace</label>
        <Select id="ws" value={workspaceId ?? ""} onChange={(e) => switchWorkspace(e.target.value)} className="h-7 text-xs">
          {me.workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </Select>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-2 py-2">
        {NAV.map((g) => (
          <div key={g.group}>
            <p className="px-2 pb-1 text-2xs font-medium uppercase tracking-wider text-faint">{g.group}</p>
            {g.items.map((it) => {
              const active = path === it.href || path.startsWith(it.href + "/") || (it.href === "/workflows" && path.startsWith("/workflows"));
              return (
                <Link
                  key={it.href}
                  href={it.href}
                  aria-current={active ? "page" : undefined}
                  className={cn("group relative flex h-8 items-center gap-2.5 rounded px-2 text-sm transition-colors", active ? "bg-hover text-fg" : "text-muted hover:bg-hover/60 hover:text-fg")}
                >
                  {active && <span className="absolute -left-2 top-1.5 h-5 w-0.5 rounded-r bg-ember" />}
                  <it.icon className={cn("h-4 w-4", active ? "text-ember" : "text-faint group-hover:text-muted")} />
                  <span className="flex-1 truncate">{it.label}</span>
                  {it.badge === "approvals" && pending > 0 && (
                    <span className="num inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-warn/20 px-1 text-2xs font-semibold text-warn" title={`${pending} approval(s) waiting`}>
                      <Hand className="mr-0.5 h-2.5 w-2.5" />{pending}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        ))}
      </div>
      <div className="border-t border-line p-2">
        <div className="flex items-center gap-2 rounded px-2 py-1.5">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-raised text-2xs font-semibold uppercase text-muted">{me.user.email[0]}</div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs text-fg">{me.user.email}</p>
            <p className="truncate text-2xs text-faint">{workspace?.role ?? "member"}</p>
          </div>
          <button onClick={toggle} className="rounded p-1 text-faint hover:bg-hover hover:text-fg" aria-label="Toggle theme">{theme === "dark" ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}</button>
          <button onClick={logout} className="rounded p-1 text-faint hover:bg-hover hover:text-fg" aria-label="Sign out"><LogOut className="h-3.5 w-3.5" /></button>
        </div>
      </div>
    </nav>
  );

  return (
    <div className="flex h-screen overflow-hidden bg-bg">
      <div className="hidden lg:block">{sidebar}</div>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
          <div className="absolute inset-y-0 left-0">{sidebar}</div>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-11 items-center gap-3 border-b border-line bg-panel px-3 lg:hidden">
          <button onClick={() => setOpen(!open)} className="rounded p-1 text-muted" aria-label="Menu">{open ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}</button>
          <Wordmark />
        </div>
        <main className="min-h-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
