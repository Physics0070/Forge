"""Environment configuration. Validated once at startup with actionable errors."""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from typing import Literal

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    env: Literal["development", "production", "test"] = Field("development", alias="FORGE_ENV")
    public_url: str = Field("http://localhost:3000", alias="FORGE_PUBLIC_URL")
    cors_origins: str = Field("http://localhost:3000", alias="FORGE_CORS_ORIGINS")
    session_secret: str = Field("", alias="FORGE_SESSION_SECRET")
    session_ttl_hours: int = Field(336, alias="FORGE_SESSION_TTL_HOURS")

    database_url: str = Field(..., alias="DATABASE_URL")

    nebius_api_key: str = Field("", alias="NEBIUS_API_KEY")
    nebius_base_url: str = Field("https://api.tokenfactory.nebius.com/v1/", alias="NEBIUS_BASE_URL")
    nebius_model_nano: str = Field("", alias="NEBIUS_MODEL_NANO")
    nebius_model_super: str = Field("", alias="NEBIUS_MODEL_SUPER")
    nebius_model_ultra: str = Field("", alias="NEBIUS_MODEL_ULTRA")
    nebius_model_default: str = Field("", alias="NEBIUS_MODEL_DEFAULT")
    nebius_max_concurrency: int = Field(4, alias="NEBIUS_MAX_CONCURRENCY")

    tavily_api_key: str = Field("", alias="TAVILY_API_KEY")
    github_client_id: str = Field("", alias="GITHUB_CLIENT_ID")
    github_client_secret: str = Field("", alias="GITHUB_CLIENT_SECRET")

    storage_endpoint: str = Field("", alias="STORAGE_ENDPOINT")
    storage_bucket: str = Field("forge-artifacts", alias="STORAGE_BUCKET")
    storage_region: str = Field("us-east-1", alias="STORAGE_REGION")
    storage_access_key: str = Field("", alias="STORAGE_ACCESS_KEY")
    storage_secret_key: str = Field("", alias="STORAGE_SECRET_KEY")

    workspaces_root: str = Field("./data/workspaces", alias="FORGE_WORKSPACES_ROOT")
    worker_concurrency: int = Field(4, alias="FORGE_WORKER_CONCURRENCY")
    job_lease_seconds: int = Field(60, alias="FORGE_JOB_LEASE_SECONDS")
    sandbox_image: str = Field("python:3.12-slim", alias="FORGE_SANDBOX_IMAGE")
    sandbox_enabled: bool = Field(True, alias="FORGE_SANDBOX_ENABLED")

    # ---- serverless (Vercel) deployment ----
    serverless: bool = Field(bool(os.environ.get("VERCEL")), alias="FORGE_SERVERLESS")
    db_pooler: Literal["none", "transaction"] = Field("none", alias="FORGE_DB_POOLER")  # pgbouncer/supavisor tx mode
    storage_driver: Literal["auto", "s3", "local", "postgres"] = Field("auto", alias="STORAGE_DRIVER")
    max_upload_mb: int = Field(100, alias="FORGE_MAX_UPLOAD_MB")
    max_repo_mb: int = Field(300, alias="FORGE_MAX_REPO_MB")
    sync_imports: bool = Field(bool(os.environ.get("VERCEL")), alias="FORGE_SYNC_IMPORTS")
    cron_secret: str = Field("", alias="FORGE_CRON_SECRET")

    @model_validator(mode="after")
    def _production_rules(self) -> "Settings":
        if self.env == "production":
            if len(self.session_secret) < 32:
                raise ValueError("FORGE_SESSION_SECRET must be at least 32 characters in production")
            if not self.nebius_api_key:
                raise ValueError("NEBIUS_API_KEY is required in production")
            if not self.public_url.startswith("https://"):
                raise ValueError("FORGE_PUBLIC_URL must be https:// in production")
        return self

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def secure_cookies(self) -> bool:
        return self.env == "production"

    def model_for_tier(self, tier: str) -> str:
        """Resolve a routing tier to a configured model id ('' when unconfigured)."""
        return {
            "nano": self.nebius_model_nano,
            "super": self.nebius_model_super,
            "ultra": self.nebius_model_ultra,
        }.get(tier, "")


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "settings"
            lines.append(f"  - {loc}: {err['msg']}")
        sys.stderr.write(
            "FORGE cannot start: invalid configuration.\n"
            + "\n".join(lines)
            + "\nSee .env.example for every variable.\n"
        )
        raise SystemExit(2) from exc
    except ValueError as exc:
        sys.stderr.write(f"FORGE cannot start: {exc}\nSee .env.example for every variable.\n")
        raise SystemExit(2) from exc
