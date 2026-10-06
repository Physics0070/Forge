"use client";
import * as React from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import type { ForgeEvent, Run } from "./types";

export interface Query<T> {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean;
  reload: () => Promise<void>;
  setData: (v: T | ((prev: T | undefined) => T | undefined)) => void;
}

/** Minimal data hook: fetch on mount / key change, optional polling, explicit reload. */
export function useQuery<T>(path: string | null, opts: { every?: number } = {}): Query<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const alive = useRef(true);
  const seq = useRef(0);

  const load = useCallback(async () => {
    if (!path) return;
    const my = ++seq.current;
    try {
      const d = await api.get<T>(path);
      if (alive.current && my === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      if (alive.current && my === seq.current) setError(e as ApiError);
    } finally {
      if (alive.current && my === seq.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    setLoading(!!path);
    setData(undefined);
    setError(null);
    load();
    return () => {
      alive.current = false;
    };
  }, [load, path]);

  useEffect(() => {
    if (!opts.every || !path) return;
    const t = setInterval(load, opts.every);
    return () => clearInterval(t);
  }, [opts.every, load, path]);

  return { data, error, loading, reload: load, setData: setData as Query<T>["setData"] };
}

const TERMINAL = new Set(["SUCCESS", "FAILED", "CANCELLED"]);

let serverlessFlag: Promise<boolean> | null = null;
function isServerless(): Promise<boolean> {
  serverlessFlag ??= fetch("/api/health", { cache: "no-store" }).then((r) => r.json()).then((j) => !!j.serverless).catch(() => false);
  return serverlessFlag;
}

/**
 * Live run state. Events arrive over Server-Sent Events (the server tails the append-only event log);
 * on each state-changing event we refetch the run snapshot (debounced) so the graph is always the
 * server's truth, never something the browser reconstructs. No periodic polling while connected.
 */
export function useLiveRun(runId: string) {
  const q = useQuery<Run>(`/api/runs/${runId}`);
  const [events, setEvents] = useState<ForgeEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const lastId = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reloadRef = useRef(q.reload);
  reloadRef.current = q.reload;

  const scheduleReload = useCallback(() => {
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      reloadRef.current();
    }, 250);
  }, []);

  useEffect(() => {
    setEvents([]);
    lastId.current = 0;
    let es: EventSource | null = null;
    let closed = false;
    const open = () => {
      es = new EventSource(`/api/runs/${runId}/stream?after=${lastId.current}`, { withCredentials: true });
      es.onopen = () => setConnected(true);
      es.addEventListener("forge", (m) => {
        const e = JSON.parse((m as MessageEvent).data) as ForgeEvent;
        if (e.id <= lastId.current) return;
        lastId.current = e.id;
        setEvents((prev) => (prev.length > 3000 ? [...prev.slice(-2500), e] : [...prev, e]));
        scheduleReload();
      });
      es.addEventListener("end", () => {
        scheduleReload();
        es?.close();
        setConnected(false);
        closed = true;
      });
      es.onerror = () => {
        setConnected(false);
        es?.close();
        if (!closed) setTimeout(() => !closed && open(), 2000); // reconnect, resuming after lastId
      };
    };
    open();
    return () => {
      closed = true;
      es?.close();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [runId, scheduleReload]);

  const active = q.data ? !TERMINAL.has(q.data.status) : true;
  const status = q.data?.status;

  // Serverless deployments have no always-on worker: while the page is open and the run is RUNNING,
  // drive execution in short server-side slices (a scheduled tick does the same in the background).
  React.useEffect(() => {
    if (status !== "RUNNING") return;
    let stop = false;
    (async () => {
      if (!(await isServerless())) return;
      while (!stop) {
        try {
          const r = await api.post<{ status: string }>(`/api/runs/${runId}/pump`);
          reloadRef.current();
          if (r.status !== "RUNNING") break;
        } catch {
          await new Promise((res) => setTimeout(res, 5000));
        }
      }
    })();
    return () => { stop = true; };
  }, [runId, status]);

  // If the event stream is unavailable (proxies, timeouts), fall back to a gentle poll while active.
  React.useEffect(() => {
    if (connected || !active) return;
    const t = setInterval(() => reloadRef.current(), 4000);
    return () => clearInterval(t);
  }, [connected, active]);

  return { run: q.data, error: q.error, loading: q.loading, events, connected, active, reload: q.reload };
}

/** Ticks once a second while `on` – used for live elapsed timers. */
export function useNow(on: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [on]);
  return now;
}

export function useLocalStorage<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(initial);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw) setV(JSON.parse(raw));
    } catch {}
  }, [key]);
  const set = useCallback(
    (nv: T) => {
      setV(nv);
      try {
        localStorage.setItem(key, JSON.stringify(nv));
      } catch {}
    },
    [key],
  );
  return [v, set];
}

export function useDebounced<T>(value: T, ms: number): T {
  const [d, setD] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setD(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return d;
}
