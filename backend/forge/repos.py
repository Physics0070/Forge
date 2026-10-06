"""Repository import: GitHub (https://github.com/owner/repo) or ZIP upload -> sanitised tar.gz snapshot."""
from __future__ import annotations

import base64
import io
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from forge.sandbox import (MAX_ARCHIVE_BYTES, MAX_ARCHIVE_FILES, MAX_ZIP_UPLOAD, SandboxError, finalize_tree,
                           minimal_env)

_GH = re.compile(r"^https://github\.com/([A-Za-z0-9_.\-]{1,100})/([A-Za-z0-9_.\-]{1,100}?)(?:\.git)?/?$")
# A single top-level folder with one of these names is repo CONTENT, not a GitHub-style wrapper folder.
_REAL_ROOT_DIRS = {"src", "lib", "app", "apps", "packages", "source", "sources", "test", "tests", "docs", "public", "cmd",
                   "pkg", "internal", "include", "scripts", "config", "backend", "frontend", "server", "client"}
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,99}$")


class RepoImportError(Exception):
    pass


@dataclass
class Snapshot:
    archive: bytes
    file_count: int
    size_bytes: int
    commit_sha: str | None = None


def parse_github_url(url: str) -> tuple[str, str]:
    m = _GH.match(url.strip())
    if not m:
        raise RepoImportError("Only https://github.com/<owner>/<repo> URLs are supported.")
    owner, repo = m.group(1), m.group(2)
    if owner in (".", "..") or repo in (".", ".."):
        raise RepoImportError("Invalid repository URL.")
    return owner, repo


def import_github(url: str, ref: str | None = None, token: str | None = None, timeout_s: int = 180) -> Snapshot:
    """Shallow-clone into a temp dir. `token` (optional PAT for private repos) is used transiently via
    git's env-config (never argv, never stored, never visible to agents)."""
    owner, repo = parse_github_url(url)
    if ref is not None and not _REF.match(ref):
        raise RepoImportError("Invalid branch/tag name.")
    clean_url = f"https://github.com/{owner}/{repo}.git"
    tmp = Path(tempfile.mkdtemp(prefix="forge-clone-"))
    try:
        env = minimal_env()
        if token:
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraheader",
                       GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}")
        cmd = ["git", "-c", "core.symlinks=false", "-c", "core.hooksPath=NUL", "-c", "protocol.allow=never",
               "-c", "protocol.https.allow=always", "clone", "--depth", "1", "--no-tags", "--single-branch",
               "--no-recurse-submodules"]
        if ref:
            cmd += ["--branch", ref]
        dest = tmp / "repo"
        cmd += ["--", clean_url, str(dest)]
        try:
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise RepoImportError("Cloning timed out.") from exc
        except FileNotFoundError as exc:
            raise RepoImportError("git is not installed on the server.") from exc
        if proc.returncode != 0:
            err = proc.stderr.replace(token or "\x00", "***")[-300:]
            raise RepoImportError(f"Could not clone the repository (is it public / is the token valid?): {err.strip()}")
        sha = None
        try:
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dest, env=minimal_env(), capture_output=True,
                                 text=True, timeout=20).stdout.strip() or None
        except Exception:
            pass
        try:
            archive, n, size = finalize_tree(dest)
        except SandboxError as exc:
            raise RepoImportError(str(exc)) from exc
        return Snapshot(archive, n, size, sha)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def import_zip(data: bytes) -> Snapshot:
    if len(data) > MAX_ZIP_UPLOAD:
        raise RepoImportError("ZIP is larger than the 100 MB upload limit.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise RepoImportError("File is not a valid ZIP archive.") from exc
    infos = zf.infolist()
    if len(infos) > MAX_ARCHIVE_FILES:
        raise RepoImportError(f"ZIP has more than {MAX_ARCHIVE_FILES} entries.")
    if sum(i.file_size for i in infos) > MAX_ARCHIVE_BYTES:
        raise RepoImportError("ZIP expands to more than the 300 MB limit (zip-bomb protection).")
    tmp = Path(tempfile.mkdtemp(prefix="forge-zip-"))
    try:
        root = (tmp / "repo")
        root.mkdir()
        root_r = root.resolve()
        for info in infos:
            name = info.filename.replace("\\", "/")
            pp = PurePosixPath(name)
            if pp.is_absolute() or ".." in pp.parts or re.match(r"^[A-Za-z]:", name) or "\x00" in name:
                raise RepoImportError(f"Unsafe path in ZIP: {name!r}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue  # symlink entry: skipped
            target = (root / pp).resolve()
            if root_r not in target.parents and target != root_r:
                raise RepoImportError(f"Unsafe path in ZIP: {name!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1 << 20)
        # If the zip wraps everything in a single top-level directory (GitHub "Download ZIP"), unwrap it.
        entries = [p for p in root.iterdir()]
        wrapper = (len(entries) == 1 and entries[0].is_dir() and entries[0].name.lower() not in _REAL_ROOT_DIRS)
        tree = entries[0] if wrapper else root
        try:
            archive, n, size = finalize_tree(tree)
        except SandboxError as exc:
            raise RepoImportError(str(exc)) from exc
        if n == 0:
            raise RepoImportError("ZIP contains no files.")
        return Snapshot(archive, n, size)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def snapshot_key(workspace_id: uuid.UUID, project_id: uuid.UUID, repo_id: uuid.UUID) -> str:
    return f"repos/{workspace_id}/{project_id}/{repo_id}.tar.gz"
