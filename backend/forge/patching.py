"""Pure-Python unified-diff application and workspace diffs (no `git` binary needed).

* `apply_patch` applies a (multi-file) unified diff atomically: every hunk of every file must match
  (trailing whitespace / CRLF tolerant, small positional fuzz) before anything is written.
* The pristine state is tracked copy-on-write in `<run dir>/.originals` (outside the agent-visible repo),
  so `workspace_diff` shows exactly what was changed relative to the imported snapshot.
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_PATH = re.compile(r"^(?:---|\+\+\+) (?:[ab]/)?(\S+)")
FUZZ = 40  # lines a hunk may have drifted from its stated position


class PatchError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Hunk:
    old_start: int
    lines: list[str] = field(default_factory=list)  # each starts with ' ', '-', '+'


@dataclass
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: list[Hunk] = field(default_factory=list)


def parse_patch(text: str) -> list[FilePatch]:
    files: list[FilePatch] = []
    cur: FilePatch | None = None
    hunk: Hunk | None = None
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old = _PATH.match(ln)
            new = _PATH.match(lines[i + 1])
            op = old.group(1) if old else None
            np = new.group(1) if new else None
            cur = FilePatch(None if op == "/dev/null" else op, None if np == "/dev/null" else np)
            files.append(cur)
            hunk = None
            i += 2
            continue
        m = _HUNK.match(ln)
        if m and cur is not None:
            hunk = Hunk(int(m.group(1)))
            cur.hunks.append(hunk)
        elif hunk is not None and ln[:1] in (" ", "-", "+"):
            hunk.lines.append(ln)
        elif hunk is not None and ln == "" and i < len(lines) - 1:
            hunk.lines.append(" ")  # a blank context line whose leading space was stripped
        i += 1
    return [f for f in files if f.hunks or f.old_path is None or f.new_path is None]


def _norm(s: str) -> str:
    return s.rstrip()


def _apply_hunks(original: list[str], fp: FilePatch) -> list[str]:
    out = list(original)
    offset = 0
    for h in fp.hunks:
        old = [ln[1:] for ln in h.lines if ln[0] in (" ", "-")]
        new = [ln[1:] for ln in h.lines if ln[0] in (" ", "+")]
        want = max(0, h.old_start - 1 + offset) if h.old_start else 0
        pos = None
        if not old:
            pos = min(want, len(out))
        else:
            for d in range(0, FUZZ + 1):
                for cand in ((want + d,) if d == 0 else (want - d, want + d)):
                    if 0 <= cand <= len(out) - len(old) and all(_norm(out[cand + k]) == _norm(old[k]) for k in range(len(old))):
                        pos = cand
                        break
                if pos is not None:
                    break
        if pos is None:
            raise PatchError("patch_does_not_apply",
                             f"Hunk at line {h.old_start} of {fp.new_path or fp.old_path} does not match the current file.")
        out[pos:pos + len(old)] = new
        offset += len(new) - len(old)
    return out


def _read_lines(p: Path) -> tuple[list[str], str, bool]:
    raw = p.read_bytes().decode("utf-8", errors="replace")
    eol = "\r\n" if "\r\n" in raw else "\n"
    trailing = raw.endswith(("\n", "\r\n"))
    return raw.replace("\r\n", "\n").split("\n")[: -1 if trailing else None], eol, trailing


# ------------------------------------------------------------------ baseline
def _orig_dir(repo: Path) -> Path:
    return repo.parent / ".originals"


def init_baseline(repo: Path) -> None:
    d = _orig_dir(repo)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text("{}")


def _manifest(repo: Path) -> dict[str, str]:
    f = _orig_dir(repo) / "manifest.json"
    return json.loads(f.read_text()) if f.exists() else {}


def _record_original(repo: Path, rel: str) -> None:
    d = _orig_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    man = _manifest(repo)
    if rel in man:
        return
    src = repo / rel
    if src.exists():
        dst = d / "files" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        man[rel] = "existed"
    else:
        man[rel] = "new"
    (d / "manifest.json").write_text(json.dumps(man))


def apply_patch(repo: Path, patch: str, resolve: Callable[[str], Path]) -> list[str]:
    """Apply atomically. `resolve(rel)` must sandbox-check paths (raises on escape)."""
    files = parse_patch(patch)
    if not files:
        raise PatchError("bad_patch", "No file changes found in the patch (expected a unified diff).")
    staged: list[tuple[str, Path, str | None]] = []
    for fp in files:
        rel = fp.new_path or fp.old_path
        assert rel
        target = resolve(rel)
        if fp.old_path and not target.exists():
            raise PatchError("patch_does_not_apply", f"{rel} does not exist in the workspace.")
        if fp.new_path is None:  # deletion
            staged.append((rel, target, None))
            continue
        if fp.old_path is None:
            original, eol, trailing = [], "\n", True
        else:
            original, eol, trailing = _read_lines(target)
        new_lines = _apply_hunks(original, fp)
        staged.append((rel, target, eol.join(new_lines) + (eol if trailing or not original else "")))
    for rel, target, content in staged:  # all hunks matched: now write
        _record_original(repo, rel)
        if content is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))
    return sorted({rel for rel, _, _ in staged})


def workspace_diff(repo: Path) -> str:
    """Unified diff of everything changed since `init_baseline` (computed by FORGE, not reported by agents)."""
    chunks: list[str] = []
    for rel, kind in sorted(_manifest(repo).items()):
        before_p = _orig_dir(repo) / "files" / rel
        after_p = repo / rel
        before = before_p.read_text("utf-8", errors="replace").replace("\r\n", "\n").splitlines(True) if kind == "existed" else []
        after = after_p.read_text("utf-8", errors="replace").replace("\r\n", "\n").splitlines(True) if after_p.exists() else []
        if before == after:
            continue
        a = f"a/{rel}" if kind == "existed" else "/dev/null"
        b = f"b/{rel}" if after_p.exists() else "/dev/null"
        diff = list(difflib.unified_diff(before, after, a, b))
        chunks.append(f"diff --git a/{rel} b/{rel}\n" + "".join(x if x.endswith("\n") else x + "\n" for x in diff))
    return "".join(chunks)


def diff_stat(repo: Path) -> str:
    lines = []
    for block in workspace_diff(repo).split("diff --git ")[1:]:
        name = block.split("\n", 1)[0].split(" b/")[-1]
        adds = sum(1 for ln in block.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        dels = sum(1 for ln in block.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        lines.append(f" {name} | +{adds} -{dels}")
    return "\n".join(lines)
