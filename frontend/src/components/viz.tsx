"use client";
import * as React from "react";
import hljs from "highlight.js/lib/core";
import python from "highlight.js/lib/languages/python";
import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import json from "highlight.js/lib/languages/json";
import yaml from "highlight.js/lib/languages/yaml";
import bash from "highlight.js/lib/languages/bash";
import go from "highlight.js/lib/languages/go";
import java from "highlight.js/lib/languages/java";
import rust from "highlight.js/lib/languages/rust";
import xml from "highlight.js/lib/languages/xml";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { CopyButton } from "@/components/ui/primitives";

hljs.registerLanguage("python", python);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("json", json);
hljs.registerLanguage("yaml", yaml);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("go", go);
hljs.registerLanguage("java", java);
hljs.registerLanguage("rust", rust);
hljs.registerLanguage("xml", xml);

const EXT_LANG: Record<string, string> = {
  py: "python", js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript", json: "json", yml: "yaml", yaml: "yaml",
  sh: "bash", go: "go", java: "java", rs: "rust", html: "xml", xml: "xml",
};
export const langOf = (path: string) => EXT_LANG[path.split(".").pop()?.toLowerCase() ?? ""];

/** Returns HTML that is safe to inject: highlight.js escapes its input, and the fallback escapes & < > itself.
 *  Repository content is untrusted, so this is the ONLY place raw HTML is produced and it never passes through unescaped. */
function highlight(code: string, lang?: string): string {
  try {
    if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
  } catch {}
  return code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/* ── Code ───────────────────────────────────────────────────────────────── */
export function CodeBlock({ code, lang, className, maxHeight = 420, lineNumbers }: { code: string; lang?: string; className?: string; maxHeight?: number; lineNumbers?: boolean }) {
  const html = React.useMemo(() => highlight(code, lang), [code, lang]);
  const lines = html.split("\n");
  return (
    <div className={cn("group relative overflow-hidden rounded border border-line bg-bg", className)}>
      <div className="absolute right-1.5 top-1.5 z-10 opacity-0 transition-opacity group-hover:opacity-100"><CopyButton text={code} /></div>
      <pre className="hljs mono overflow-auto p-3 text-xs leading-[18px]" style={{ maxHeight }}>
        {lineNumbers ? (
          <code>
            {lines.map((l, i) => (
              <div key={i} className="flex">
                <span className="mr-3 w-8 shrink-0 select-none text-right text-faint">{i + 1}</span>
                <span className="whitespace-pre" dangerouslySetInnerHTML={{ __html: l || " " }} />
              </div>
            ))}
          </code>
        ) : (
          <code dangerouslySetInnerHTML={{ __html: html }} />
        )}
      </pre>
    </div>
  );
}

/* ── JSON tree ──────────────────────────────────────────────────────────── */
function Scalar({ v }: { v: unknown }) {
  if (v === null) return <span className="text-faint">null</span>;
  if (typeof v === "string") return <span className="whitespace-pre-wrap break-words text-ok">&quot;{v.length > 600 ? v.slice(0, 600) + `… (+${v.length - 600})` : v}&quot;</span>;
  if (typeof v === "number") return <span className="text-warn">{v}</span>;
  if (typeof v === "boolean") return <span className="text-ember">{String(v)}</span>;
  return <span>{String(v)}</span>;
}
function Node({ k, v, depth, defaultOpen }: { k?: string; v: unknown; depth: number; defaultOpen: boolean }) {
  const isObj = v !== null && typeof v === "object";
  const [open, setOpen] = React.useState(defaultOpen);
  if (!isObj) {
    return (
      <div className="flex gap-1.5 py-px" style={{ paddingLeft: depth * 14 }}>
        {k !== undefined && <span className="shrink-0 text-muted">{k}:</span>}
        <Scalar v={v} />
      </div>
    );
  }
  const entries = Array.isArray(v) ? v.map((x, i) => [String(i), x] as const) : Object.entries(v as object);
  const summary = Array.isArray(v) ? `[${entries.length}]` : `{${entries.length}}`;
  return (
    <div>
      <button className="flex items-center gap-1 py-px hover:text-fg" style={{ paddingLeft: depth * 14 - 2 }} onClick={() => setOpen(!open)}>
        {open ? <ChevronDown className="h-3 w-3 text-faint" /> : <ChevronRight className="h-3 w-3 text-faint" />}
        {k !== undefined && <span className="text-muted">{k}:</span>}
        {!open && <span className="text-faint">{summary}</span>}
      </button>
      {open && entries.map(([ek, ev]) => <Node key={ek} k={ek} v={ev} depth={depth + 1} defaultOpen={depth < 1} />)}
      {open && entries.length === 0 && <div className="text-faint" style={{ paddingLeft: (depth + 1) * 14 }}>empty</div>}
    </div>
  );
}
export function JsonTree({ data, className, maxHeight = 420 }: { data: unknown; className?: string; maxHeight?: number }) {
  return (
    <div className={cn("group relative overflow-auto rounded border border-line bg-bg p-2.5 mono text-xs", className)} style={{ maxHeight }}>
      <div className="absolute right-1.5 top-1.5 opacity-0 group-hover:opacity-100"><CopyButton text={JSON.stringify(data, null, 2)} /></div>
      <Node v={data} depth={0} defaultOpen />
    </div>
  );
}

/* ── Markdown ───────────────────────────────────────────────────────────── */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-forge text-sm">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
        {children}
      </ReactMarkdown>
    </div>
  );
}

/* ── Side-by-side diff ──────────────────────────────────────────────────── */
interface Row { l: string | null; r: string | null; ln: number | null; rn: number | null; kind: "ctx" | "del" | "add" | "chg" | "hunk" }

interface Hunk { oldStart: number; oldLines: number; newStart: number; newLines: number; lines: string[] }

function rowsFor(hunks: Hunk[]): Row[] {
  const out: Row[] = [];
  for (const h of hunks) {
    out.push({ l: `@@ -${h.oldStart},${h.oldLines} +${h.newStart},${h.newLines} @@`, r: null, ln: null, rn: null, kind: "hunk" });
    let ol = h.oldStart, nl = h.newStart;
    let i = 0;
    const lines = h.lines;
    while (i < lines.length) {
      const c = lines[i][0];
      if (c === " ") { out.push({ l: lines[i].slice(1), r: lines[i].slice(1), ln: ol++, rn: nl++, kind: "ctx" }); i++; continue; }
      const dels: string[] = [], adds: string[] = [];
      while (i < lines.length && lines[i][0] === "-") dels.push(lines[i++].slice(1));
      while (i < lines.length && lines[i][0] === "+") adds.push(lines[i++].slice(1));
      if (i < lines.length && lines[i][0] === "\\") i++;
      const n = Math.max(dels.length, adds.length);
      for (let k = 0; k < n; k++) {
        const d = dels[k], a = adds[k];
        out.push({ l: d ?? null, r: a ?? null, ln: d !== undefined ? ol++ : null, rn: a !== undefined ? nl++ : null, kind: d !== undefined && a !== undefined ? "chg" : d !== undefined ? "del" : "add" });
      }
    }
  }
  return out;
}

/** Parses a unified diff (possibly multi-file) and renders old | new columns. */
export function DiffView({ patch, maxHeight = 520 }: { patch: string; maxHeight?: number }) {
  const files = React.useMemo(() => splitFiles(patch), [patch]);
  if (!patch.trim()) return <p className="text-sm text-faint">No changes.</p>;
  return (
    <div className="space-y-3">
      {files.map((f, idx) => (
        <div key={idx} className="overflow-hidden rounded border border-line">
          <div className="flex items-center justify-between border-b border-line bg-raised px-3 py-1.5">
            <span className="mono text-xs">{f.name}</span>
            <span className="num text-xs"><span className="text-ok">+{f.adds}</span> <span className="text-bad">−{f.dels}</span></span>
          </div>
          <div className="overflow-auto bg-bg" style={{ maxHeight }}>
            <table className="mono w-full table-fixed border-collapse text-xs leading-[18px]">
              <colgroup><col className="w-10" /><col /><col className="w-10" /><col /></colgroup>
              <tbody>
                {f.rows.map((r, i) =>
                  r.kind === "hunk" ? (
                    <tr key={i}><td colSpan={4} className="bg-info/10 px-3 py-0.5 text-info">{r.l}</td></tr>
                  ) : (
                    <tr key={i}>
                      <td className={cn("select-none px-1.5 text-right text-faint", r.l !== null && r.kind !== "ctx" && "bg-bad/10")}>{r.ln ?? ""}</td>
                      <td className={cn("whitespace-pre-wrap break-all px-2", r.kind !== "ctx" && r.l !== null && "bg-bad/10 text-bad/90", r.l === null && "bg-raised/40")}>{r.l ?? ""}</td>
                      <td className={cn("select-none border-l border-line px-1.5 text-right text-faint", r.r !== null && r.kind !== "ctx" && "bg-ok/10")}>{r.rn ?? ""}</td>
                      <td className={cn("whitespace-pre-wrap break-all px-2", r.kind !== "ctx" && r.r !== null && "bg-ok/10 text-ok/90", r.r === null && "bg-raised/40")}>{r.r ?? ""}</td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

function splitFiles(patch: string): { name: string; rows: Row[]; adds: number; dels: number }[] {
  const chunks = patch.split(/^(?=diff --git |--- )/m).filter((c) => c.trim());
  const out: { name: string; rows: Row[]; adds: number; dels: number }[] = [];
  for (const c of chunks) {
    if (c.startsWith("--- ") === false && !c.startsWith("diff --git")) continue;
    try {
      const hunks = parseHunks(c);
      const name = (c.match(/^\+\+\+ (?:b\/)?(\S+)/m)?.[1] ?? c.match(/^--- (?:a\/)?(\S+)/m)?.[1] ?? "file");
      const rows = rowsFor(hunks);
      out.push({ name, rows, adds: rows.filter((r) => r.r !== null && r.kind !== "ctx" && r.kind !== "hunk").length, dels: rows.filter((r) => r.l !== null && r.kind !== "ctx" && r.kind !== "hunk").length });
    } catch {
      out.push({ name: "patch", rows: [{ l: c, r: null, ln: null, rn: null, kind: "ctx" }], adds: 0, dels: 0 });
    }
  }
  return out;
}

function parseHunks(chunk: string): Hunk[] {
  const hunks: Hunk[] = [];
  let cur: Hunk | null = null;
  for (const line of chunk.split("\n")) {
    const m = line.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/);
    if (m) {
      cur = { oldStart: +m[1], oldLines: m[2] === undefined ? 1 : +m[2], newStart: +m[3], newLines: m[4] === undefined ? 1 : +m[4], lines: [] };
      hunks.push(cur);
    } else if (cur && /^[ +\-\\]/.test(line)) cur.lines.push(line);
  }
  return hunks;
}
