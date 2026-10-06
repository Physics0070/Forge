"""Filesystem sandbox: every agent-reachable path is resolved through `safe_resolve`.

Layout (the ONLY place agents can touch):
    {WORKSPACES_ROOT}/{workspace_id}/projects/{project_id}/runs/{run_id}/repo/
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tarfile
import uuid
from pathlib import Path

from forge.config import get_settings

MAX_FILE_BYTES = 2 * 1024 * 1024  # a single file agents may read in one call
SKIP_DIRS = {".git", "node_modules", "vendor", "__pycache__", ".venv", "venv", "dist", "build", ".next", "target", ".tox"}
BLOCKED_SEGMENTS = {".git"}  # agents never see git internals
MAX_ARCHIVE_FILES = 20_000
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024
MAX_ZIP_UPLOAD = 100 * 1024 * 1024


class SandboxError(Exception):
    """Raised for any attempted escape or limit violation. Message is safe to show to users."""


def workspace_root() -> Path:
    return Path(get_settings().workspaces_root).resolve()


def run_repo_dir(workspace_id: uuid.UUID, project_id: uuid.UUID, run_id: uuid.UUID) -> Path:
    for part in (workspace_id, project_id, run_id):
        if not isinstance(part, uuid.UUID):
            raise SandboxError("ids must be UUIDs")  # never build paths from free-form strings
    return workspace_root() / str(workspace_id) / "projects" / str(project_id) / "runs" / str(run_id) / "repo"


def safe_resolve(base: Path, rel: str, *, must_exist: bool = False) -> Path:
    """Resolve `rel` under `base` or raise. Blocks absolute paths, `..`, symlink escapes, `.git`, NUL bytes."""
    if not isinstance(rel, str) or "\x00" in rel or len(rel) > 1000:
        raise SandboxError("invalid path")
    rel_norm = rel.replace("\\", "/")
    if re.match(r"^([A-Za-z]:|/|~)", rel_norm):
        raise SandboxError("absolute paths are not allowed")
    parts = [p for p in rel_norm.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise SandboxError("path traversal is not allowed")
    if any(p.lower() in BLOCKED_SEGMENTS for p in parts):
        raise SandboxError("access to this path is not allowed")
    base_r = base.resolve()
    target = (base_r.joinpath(*parts)).resolve() if parts else base_r
    if target != base_r and base_r not in target.parents:
        raise SandboxError("path escapes the run workspace")
    # reject symlinks anywhere along the path
    cur = base_r
    for p in parts:
        cur = cur / p
        if cur.is_symlink():
            raise SandboxError("symlinks are not allowed")
    if must_exist and not target.exists():
        raise SandboxError("path does not exist")
    return target


def rel_path(base: Path, p: Path) -> str:
    return p.resolve().relative_to(base.resolve()).as_posix()


def iter_files(base: Path, glob: str | None = None):
    """Yield (relpath, Path) for regular, non-skipped files, deterministic order."""
    import fnmatch

    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (Path(root) / d).is_symlink())
        for f in sorted(files):
            p = Path(root) / f
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(base).as_posix()
            if glob and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(f, glob)):
                continue
            yield rel, p


def is_probably_text(path: Path, sniff: int = 4096) -> bool:
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(sniff)
    except OSError:
        return False
    return b"\x00" not in chunk


def read_text_limited(path: Path, limit: int = MAX_FILE_BYTES) -> str:
    with open(path, "rb") as fh:
        data = fh.read(limit + 1)
    return data[:limit].decode("utf-8", errors="replace")


# ----------------------------------------------------------- archives
def minimal_env() -> dict[str, str]:
    """Environment for child processes: never inherits secrets (API keys, DATABASE_URL, ...)."""
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "LANG", "COMSPEC", "PATHEXT", "WINDIR")
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def finalize_tree(tree: Path) -> tuple[bytes, int, int]:
    """Sanitise an extracted/cloned tree in place and return (tar.gz bytes, file_count, total_bytes).

    Removes .git, symlinks and special files; enforces file-count and size limits.
    """
    shutil.rmtree(tree / ".git", ignore_errors=True)
    count = total = 0
    for root, dirs, files in os.walk(tree, topdown=True):
        for d in list(dirs):
            dp = Path(root) / d
            if dp.is_symlink():
                dp.unlink()
                dirs.remove(d)
        for f in files:
            fp = Path(root) / f
            if fp.is_symlink() or not fp.is_file():
                fp.unlink(missing_ok=True)
                continue
            count += 1
            total += fp.stat().st_size
            if count > MAX_ARCHIVE_FILES:
                raise SandboxError(f"repository has more than {MAX_ARCHIVE_FILES} files")
            if total > MAX_ARCHIVE_BYTES:
                raise SandboxError("repository is larger than the 300 MB limit")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(tree, arcname=".", recursive=True)
    return buf.getvalue(), count, total


def extract_archive(data: bytes, dest: Path) -> None:
    """Extract our own tar.gz with Python's `data` filter (blocks absolute paths, links, devices)."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        total = 0
        for m in tar.getmembers():
            total += max(m.size, 0)
            if total > MAX_ARCHIVE_BYTES:
                raise SandboxError("archive too large")
        tar.extractall(dest, filter="data")


def git_baseline(repo_dir: Path) -> None:
    """Commit the pristine snapshot so later `git diff` shows exactly what agents changed."""
    env = minimal_env()
    cmds = [
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.name=forge", "-c", "user.email=forge@localhost", "-c", "commit.gpgsign=false",
         "commit", "-q", "--allow-empty", "-m", "baseline"],
    ]
    for c in cmds:
        subprocess.run(c, cwd=repo_dir, env=env, check=True, capture_output=True, timeout=120)
