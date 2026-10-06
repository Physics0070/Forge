# FORGE backend image: API, worker and migrations share one image (different commands).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# git: repository import + isolated patch application. docker CLI: optional test sandbox (talks to a socket you mount).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl docker.io \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install "fastapi>=0.115" "uvicorn[standard]>=0.30" "sqlalchemy>=2.0.30" "psycopg[binary]>=3.2" \
    "alembic>=1.13" "pydantic>=2.8" "pydantic-settings>=2.4" "argon2-cffi>=23.1" "httpx>=0.27" "boto3>=1.34" "jsonschema>=4.22" \
    "python-multipart>=0.0.9" "email-validator>=2.2" "regex>=2024.5"
COPY forge ./forge
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-deps .

# Never run as root. Run workspaces live on a volume owned by this user.
RUN useradd --create-home --uid 10001 forge && mkdir -p /srv/forge/workspaces && chown -R forge /srv/forge /app
USER forge
ENV FORGE_WORKSPACES_ROOT=/srv/forge/workspaces

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD curl -fsS http://localhost:8000/api/health || exit 1
# default: API. Worker: `python -m forge.engine.worker`. Migrations: `alembic upgrade head`.
CMD ["uvicorn", "forge.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
