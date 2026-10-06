"""Object storage for repository snapshots and large artifacts.

S3-compatible (MinIO locally, Nebius Object Storage in production). When STORAGE_ENDPOINT is unset
a local-filesystem driver is used: fine for single-host dev, NOT for multi-host production
(API and workers must share storage) — SECURITY.md/DEPLOYMENT.md document this.
"""
from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from forge.config import get_settings


class ObjectStore(ABC):
    driver: str

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    @abstractmethod
    def get(self, key: str) -> bytes: ...
    @abstractmethod
    def delete(self, key: str) -> None: ...
    @abstractmethod
    def exists(self, key: str) -> bool: ...

    def put_file(self, key: str, path: Path) -> None:
        self.put(key, path.read_bytes())


def _safe_key(key: str) -> str:
    if key.startswith("/") or ".." in key.split("/") or "\\" in key or "\x00" in key:
        raise ValueError(f"unsafe storage key: {key!r}")
    return key


class LocalStore(ObjectStore):
    driver = "local"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self.root / _safe_key(key)).resolve()
        if self.root not in p.parents:
            raise ValueError("storage key escapes root")
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def get(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._p(key).exists()


class S3Store(ObjectStore):
    driver = "s3"

    def __init__(self, endpoint: str, bucket: str, region: str, access_key: str, secret_key: str):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.client = boto3.client(
            "s3", endpoint_url=endpoint or None, region_name=region, aws_access_key_id=access_key,
            aws_secret_access_key=secret_key, config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )
        try:
            self.client.head_bucket(Bucket=bucket)
        except Exception:
            self.client.create_bucket(Bucket=bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(Bucket=self.bucket, Key=_safe_key(key), Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=_safe_key(key))["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_safe_key(key))

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_safe_key(key))
            return True
        except Exception:
            return False


class PgStore(ObjectStore):
    """Objects stored in Postgres (`blobs` table). For serverless deployments without an S3 bucket;
    every API/worker instance shares it, unlike the local driver."""

    driver = "postgres"

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        from sqlalchemy import text

        from forge.db import session_scope

        with session_scope() as db:
            db.execute(text("""INSERT INTO blobs (key, data, content_type, size_bytes) VALUES (:k, :d, :t, :n)
                               ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, content_type = EXCLUDED.content_type,
                               size_bytes = EXCLUDED.size_bytes"""), {"k": _safe_key(key), "d": data, "t": content_type, "n": len(data)})

    def get(self, key: str) -> bytes:
        from sqlalchemy import text

        from forge.db import session_scope

        with session_scope() as db:
            row = db.execute(text("SELECT data FROM blobs WHERE key = :k"), {"k": _safe_key(key)}).first()
        if row is None:
            raise FileNotFoundError(key)
        return bytes(row[0])

    def delete(self, key: str) -> None:
        from sqlalchemy import text

        from forge.db import session_scope

        with session_scope() as db:
            db.execute(text("DELETE FROM blobs WHERE key = :k"), {"k": _safe_key(key)})

    def exists(self, key: str) -> bool:
        from sqlalchemy import text

        from forge.db import session_scope

        with session_scope() as db:
            return db.execute(text("SELECT 1 FROM blobs WHERE key = :k"), {"k": _safe_key(key)}).first() is not None


@lru_cache
def get_store() -> ObjectStore:
    s = get_settings()
    driver = s.storage_driver
    if driver == "auto":
        driver = "s3" if s.storage_endpoint else "local"
    if driver == "s3":
        return S3Store(s.storage_endpoint, s.storage_bucket, s.storage_region, s.storage_access_key, s.storage_secret_key)
    if driver == "postgres":
        return PgStore()
    return LocalStore(Path(s.workspaces_root).parent / "objects")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
