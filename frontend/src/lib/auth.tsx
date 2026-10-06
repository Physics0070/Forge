"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ApiError, api, setSession, setUnauthenticatedHandler } from "./api";
import type { Me } from "./types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  workspaceId: string | null;
  workspace: Me["workspaces"][number] | null;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, workspaceName: string) => Promise<void>;
  logout: () => Promise<void>;
  switchWorkspace: (id: string) => void;
  refresh: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);
const WS_KEY = "forge.workspace";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const router = useRouter();
  const path = usePathname();

  const apply = useCallback((m: Me) => {
    let ws = m.active_workspace_id ?? m.workspaces[0]?.id ?? null;
    try {
      const saved = localStorage.getItem(WS_KEY);
      if (saved && m.workspaces.some((w) => w.id === saved)) ws = saved; // only a selector: the server re-checks membership
    } catch {}
    setSession(m.csrf_token, ws);
    setWorkspaceId(ws);
    setMe(m);
  }, []);

  const refresh = useCallback(async () => {
    try {
      apply(await api.get<Me>("/api/auth/me"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setMe(null);
        setSession(null, null);
      }
    } finally {
      setLoading(false);
    }
  }, [apply]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    setUnauthenticatedHandler(() => {
      setMe(null);
      setSession(null, null);
      router.replace("/login");
    });
    return () => setUnauthenticatedHandler(null);
  }, [router]);

  useEffect(() => {
    if (!loading && !me && path !== "/login") router.replace("/login");
  }, [loading, me, path, router]);

  const value = useMemo<AuthState>(
    () => ({
      me,
      loading,
      workspaceId,
      workspace: me?.workspaces.find((w) => w.id === workspaceId) ?? null,
      login: async (email, password) => {
        apply(await api.post<Me>("/api/auth/login", { email, password }));
      },
      signup: async (email, password, workspaceName) => {
        apply(await api.post<Me>("/api/auth/signup", { email, password, workspace_name: workspaceName }));
      },
      logout: async () => {
        try {
          await api.post("/api/auth/logout");
        } finally {
          setMe(null);
          setSession(null, null);
          router.replace("/login");
        }
      },
      switchWorkspace: (id) => {
        try {
          localStorage.setItem(WS_KEY, id);
        } catch {}
        setSession(me?.csrf_token ?? null, id);
        setWorkspaceId(id);
        window.location.assign("/dashboard"); // hard reset all cached data from the previous workspace
      },
      refresh,
    }),
    [me, loading, workspaceId, apply, router, refresh],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
