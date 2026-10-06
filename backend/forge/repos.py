"""Repository import: GitHub (https://github.com/owner/repo) or ZIP upload -> sanitised tar.gz snapshot."""
from __future__ import annotations

import io
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from forge.sandbox import MAX_ARCHIVE_BYTES, MAX_ARCHIVE_FILES, MAX_ZIP_UPLOAD, SandboxError, finalize_tree

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


MAX_DOWNLOAD = 150 * 1024 * 1024
_ALLOWED_REDIRECT_HOSTS = {"codeload.github.com"}


def import_github(url: str, ref: str | None = None, token: str | None = None, timeout_s: int = 180,
                  client: "httpx.Client | None" = None) -> Snapshot:
    """Download the repository tarball over HTTPS (no git binary needed: works on serverless hosts).

    Only api.github.com is contacted, and redirects are followed only to codeload.github.com (SSRF guard).
    `token` (optional, for private repos) is sent once to GitHub, never stored or logged, never visible to agents.
    """
    import tarfile
    from urllib.parse import urlparse

    import httpx

    owner, repo = parse_github_url(url)
    if ref is not None and not _REF.match(ref):
        raise RepoImportError("Invalid branch/tag name.")
    api = f"https://api.github.com/repos/{owner}/{repo}/tarball" + (f"/{ref}" if ref else "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "forge-runtime", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    c = client or httpx.Client(timeout=httpx.Timeout(timeout_s, connect=15.0), follow_redirects=False)
    tmp = Path(tempfile.mkdtemp(prefix="forge-gh-"))
    try:
        try:
            r = c.get(api, headers=headers)
            hops = 0
            while r.status_code in (301, 302, 303, 307, 308) and hops < 3:
                loc = r.headers.get("location", "")
                u = urlparse(loc)
                if u.scheme != "https" or u.hostname not in _ALLOWED_REDIRECT_HOSTS | {"api.github.com"}:
                    raise RepoImportError("Unexpected redirect while downloading the repository.")
                r = c.get(loc, headers={"User-Agent": "forge-runtime"} if u.hostname != "api.github.com" else headers)
                hops += 1
        except httpx.HTTPError as exc:
            raise RepoImportError(f"Could not reach GitHub: {type(exc).__name__}") from exc
        if r.status_code in (401, 403, 404):
            msg = "Repository not found or not accessible (is it public / is the token valid?)."
            if r.status_code == 403 and "rate limit" in r.text.lower():
                msg = "GitHub API rate limit reached. Retry later or provide a token."
            raise RepoImportError(msg)
        if r.status_code >= 400:
            raise RepoImportError(f"GitHub returned HTTP {r.status_code}.")
        data = r.content
        if len(data) > MAX_DOWNLOAD:
            raise RepoImportError("Repository archive is too large.")
        src = tmp / "src"
        src.mkdir()
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                members = tar.getmembers()
                if len(members) > MAX_ARCHIVE_FILES * 2:
                    raise RepoImportError("Repository has too many files.")
                if sum(max(m.size, 0) for m in members) > MAX_ARCHIVE_BYTES:
                    raise RepoImportError("Repository is larger than the 300 MB limit.")
                tar.extractall(src, filter="data")  # rejects absolute paths, links outside, devices
        except tarfile.TarError as exc:
            raise RepoImportError("GitHub returned an unreadable archive.") from exc
        entries = list(src.iterdir())
        tree = entries[0] if len(entries) == 1 and entries[0].is_dir() else src
        sha = None
        m = re.match(rf"^{re.escape(owner)}-{re.escape(repo)}-([0-9a-f]{{7,40}})$", tree.name, re.I)
        if m:
            sha = m.group(1)
        try:
            archive, n, size = finalize_tree(tree)
        except SandboxError as exc:
            raise RepoImportError(str(exc)) from exc
        if n == 0:
            raise RepoImportError("Repository is empty.")
        return Snapshot(archive, n, size, sha)
    finally:
        if client is None:
            c.close()
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


def summarize_archive(archive: bytes) -> dict:
    """Cheap, content-free overview (extension histogram + top-level entries) used as compiler context."""
    import collections
    import tarfile

    exts: collections.Counter[str] = collections.Counter()
    tops: collections.Counter[str] = collections.Counter()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            parts = PurePosixPath(m.name.lstrip("./")).parts
            if parts:
                tops[parts[0]] += 1
            exts[PurePosixPath(m.name).suffix.lower() or PurePosixPath(m.name).name] += 1
    return {"extensions": dict(exts.most_common(12)), "top_level": dict(tops.most_common(12))}
