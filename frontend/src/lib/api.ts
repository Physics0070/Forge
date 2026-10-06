export class ApiError extends Error {
  status: number;
  code: string;
  body: any;
  constructor(status: number, code: string, message: string, body?: any) {
    super(message);
    this.status = status;
    this.code = code;
    this.body = body;
  }
}

let csrfToken: string | null = null;
let workspaceId: string | null = null;
let onUnauthenticated: (() => void) | null = null;

export function setSession(csrf: string | null, ws: string | null) {
  csrfToken = csrf;
  workspaceId = ws;
}
export function setUnauthenticatedHandler(fn: (() => void) | null) {
  onUnauthenticated = fn;
}

async function request<T>(method: string, path: string, body?: unknown, extra?: { headers?: Record<string, string>; form?: FormData }): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...(extra?.headers ?? {}) };
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  if (workspaceId) headers["X-Workspace-Id"] = workspaceId;
  let payload: BodyInit | undefined;
  if (extra?.form) payload = extra.form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: payload, credentials: "same-origin", cache: "no-store" });
  } catch {
    throw new ApiError(0, "NETWORK", "Cannot reach the FORGE API. Check your connection and that the backend is running.");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const err = data?.error ?? {};
    if (res.status === 401 && onUnauthenticated && !path.startsWith("/api/auth/")) onUnauthenticated();
    throw new ApiError(res.status, err.code ?? "ERROR", err.message ?? `Request failed (${res.status})`, err);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown, headers?: Record<string, string>) => request<T>("POST", path, body ?? {}, { headers }),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body ?? {}),
  del: <T = void>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<T>("POST", path, undefined, { form });
  },
};

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}
