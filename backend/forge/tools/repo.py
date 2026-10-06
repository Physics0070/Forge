"""Repository tools. All paths go through sandbox.safe_resolve; the upstream repo is never touched."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import regex

from forge.sandbox import MAX_FILE_BYTES, SandboxError, is_probably_text, iter_files, read_text_limited, rel_path, safe_resolve
from forge.tools.base import ToolContext, ToolError, clip

MAX_READ_LINES = 400
MAX_DIR_ENTRIES = 200
MAX_SEARCH_RESULTS = 200
SEARCH_TIME_BUDGET_S = 8.0
MAX_PATCH_BYTES = 200 * 1024


def _sb(fn):
    try:
        return fn()
    except SandboxError as exc:
        raise ToolError("sandbox_violation", str(exc)) from exc


def repository_read(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    base = ctx.require_repo()
    path = _sb(lambda: safe_resolve(base, args["path"], must_exist=True))
    if path.is_dir():
        entries = []
        for child in sorted(path.iterdir())[:MAX_DIR_ENTRIES]:
            if child.name == ".git" or child.is_symlink():
                continue
            entries.append({"name": child.name + ("/" if child.is_dir() else ""), "size": child.stat().st_size if child.is_file() else None})
        return {"path": rel_path(base, path) if path != base.resolve() else ".", "type": "directory", "entries": entries}
    if not is_probably_text(path):
        raise ToolError("binary_file", "Binary files cannot be read.")
    text = read_text_limited(path)
    lines = text.splitlines()
    total = len(lines)
    start = max(1, int(args.get("start_line") or 1))
    end = min(total, int(args.get("end_line") or (start + MAX_READ_LINES - 1)), start + MAX_READ_LINES - 1)
    numbered = "\n".join(f"{i:>5}| {lines[i - 1]}" for i in range(start, end + 1))
    body, truncated = clip(numbered)
    return {"path": rel_path(base, path), "type": "file", "start_line": start, "end_line": end, "total_lines": total,
            "content": body, "truncated": truncated or end < total, "file_too_large": path.stat().st_size > MAX_FILE_BYTES}


def file_search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    base = ctx.require_repo()
    glob = str(args["glob"])[:200]
    out = []
    for rel, p in iter_files(base, glob):
        out.append({"path": rel, "size": p.stat().st_size})
        if len(out) >= MAX_SEARCH_RESULTS:
            break
    return {"glob": glob, "count": len(out), "files": out, "truncated": len(out) >= MAX_SEARCH_RESULTS}


def code_search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    base = ctx.require_repo()
    pattern = str(args["pattern"])
    if len(pattern) > 300:
        raise ToolError("bad_pattern", "Pattern is too long (max 300 chars).")
    try:
        rx = regex.compile(pattern, regex.IGNORECASE)
    except regex.error as exc:
        raise ToolError("bad_pattern", f"Invalid regular expression: {exc}") from exc
    max_results = min(int(args.get("max_results") or 50), MAX_SEARCH_RESULTS)
    deadline = time.monotonic() + SEARCH_TIME_BUDGET_S
    matches: list[dict[str, Any]] = []
    files_scanned = 0
    for rel, p in iter_files(base, args.get("glob")):
        if time.monotonic() > deadline:
            break
        if p.stat().st_size > MAX_FILE_BYTES or not is_probably_text(p):
            continue
        files_scanned += 1
        for i, line in enumerate(read_text_limited(p).splitlines(), 1):
            if len(line) > 2000:
                continue
            try:
                hit = rx.search(line, timeout=0.05)  # ReDoS guard: model-supplied regex
            except TimeoutError:
                raise ToolError("bad_pattern", "Pattern is too expensive to evaluate.") from None
            if hit:
                matches.append({"file": rel, "line": i, "text": line.strip()[:240]})
                if len(matches) >= max_results:
                    return {"pattern": pattern, "files_scanned": files_scanned, "matches": matches, "truncated": True}
    return {"pattern": pattern, "files_scanned": files_scanned, "matches": matches,
            "truncated": time.monotonic() > deadline}


_DIFF_PATH = re.compile(r"^(?:---|\+\+\+) (?:[ab]/)?(\S+)", re.M)


def _patch_paths(patch: str) -> list[str]:
    paths = []
    for m in _DIFF_PATH.finditer(patch):
        p = m.group(1)
        if p != "/dev/null":
            paths.append(p)
    return sorted(set(paths))


def repository_write(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Apply a unified diff to the run's *isolated copy*. Every path is sandbox-validated; application is atomic."""
    from forge.patching import PatchError, apply_patch, diff_stat

    base = ctx.require_repo()
    patch = args["patch"]
    if len(patch.encode()) > MAX_PATCH_BYTES:
        raise ToolError("patch_too_large", "Patch exceeds 200 KB.")
    paths = _patch_paths(patch)
    if not paths:
        raise ToolError("bad_patch", "No file paths found in the patch (expected a unified diff).")
    for p in paths:
        _sb(lambda p=p: safe_resolve(base, p))
    try:
        applied = apply_patch(base, patch, lambda rel: _sb(lambda: safe_resolve(base, rel)))
    except PatchError as exc:
        raise ToolError(exc.code, exc.message) from exc
    ctx.applied_patches.append(patch if patch.endswith("\n") else patch + "\n")
    return {"applied_files": applied, "diff_stat": diff_stat(base)[-1500:]}


def current_diff(repo_dir: Path) -> str:
    """Full unified diff of everything agents changed vs. the pristine snapshot (computed by FORGE)."""
    from forge.patching import workspace_diff

    return workspace_diff(repo_dir)
