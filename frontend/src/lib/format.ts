/** Formatting helpers. The one hard rule: a missing value is "Not available", never 0 and never a guess. */
export const NA = "Not available";

export function fmtUsd(v: number | null | undefined, digits = 4): string {
  if (v === null || v === undefined) return NA;
  if (v === 0) return "$0.00";
  return `$${v < 0.01 ? v.toFixed(digits) : v.toFixed(Math.min(digits, 4))}`;
}
export function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined) return NA;
  return new Intl.NumberFormat("en-US").format(v);
}
export function fmtCompact(v: number | null | undefined): string {
  if (v === null || v === undefined) return NA;
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v);
}
export function fmtPct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return NA;
  return `${(v * 100).toFixed(digits)}%`;
}
export function fmtMs(v: number | null | undefined): string {
  if (v === null || v === undefined) return NA;
  return v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(v < 10_000 ? 2 : 1)} s`;
}
export function fmtDuration(s: number | null | undefined): string {
  if (s === null || s === undefined) return NA;
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  if (m < 60) return `${m}m ${r.toString().padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${(m % 60).toString().padStart(2, "0")}m`;
}
export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return NA;
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
export function fmtRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return NA;
  const d = (now - new Date(iso).getTime()) / 1000;
  if (d < 5) return "just now";
  if (d < 60) return `${Math.floor(d)}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return NA;
  return new Date(iso).toLocaleTimeString(undefined, { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return NA;
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}
export const shortId = (id: string | null | undefined, n = 8) => (id ? id.slice(0, n) : NA);
export const titleCase = (s: string) => s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
export const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

export function durationBetween(start: string | null, end: string | null, now = Date.now()): number | null {
  if (!start) return null;
  return ((end ? new Date(end).getTime() : now) - new Date(start).getTime()) / 1000;
}

export function downloadText(filename: string, text: string, mime = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
