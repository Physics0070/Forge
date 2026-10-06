# Deployment on Nebius AI Cloud

Target: one Nebius Compute VM running Docker Compose (Caddy + web + api + worker), with **Nebius Managed Service for PostgreSQL** and **Nebius Object Storage**. Inference: **Nebius Token Factory**. Scale out by adding worker VMs pointed at the same database and bucket.

> Items marked **[YOU]** need your Nebius account. They're placeholders FORGE cannot create for you.

## 1. Token Factory **[YOU]**
1. Redeem credits (activation code `NEBIUS-DEVPOST-GLOBAL26`) and create an API key in the Token Factory console.
2. List the model ids available to your account:
   ```bash
   curl -s https://api.tokenfactory.nebius.com/v1/models -H "Authorization: Bearer $NEBIUS_API_KEY" | jq -r '.data[].id' | grep -i nemotron
   ```
3. Put the Nano / Super / Ultra Nemotron ids into `NEBIUS_MODEL_NANO`, `NEBIUS_MODEL_SUPER`, `NEBIUS_MODEL_ULTRA` (`nvidia/nemotron-3-super-120b-a12b` is the Super id published by Nebius).
4. Optional but recommended: copy per-million-token prices from the console's Prices page into `backend/forge/providers/pricing.json`. Without it, cost shows "Not available" and only token budgets are enforced.

## 2. Managed PostgreSQL **[YOU]**
Create a Managed Service for PostgreSQL cluster (v16) in the same region as the VM, a database `forge` and a user. Set
`DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/forge?sslmode=require`.

## 3. Object Storage **[YOU]**
Create a bucket (e.g. `forge-artifacts`) and a static access key for a service account with access to it:
```
STORAGE_ENDPOINT=https://storage.<region>.nebius.cloud
STORAGE_REGION=<region>            # e.g. eu-north1
STORAGE_BUCKET=forge-artifacts
STORAGE_ACCESS_KEY=…
STORAGE_SECRET_KEY=…
```

## 4. Compute VM **[YOU]**
Ubuntu 22.04+, 4 vCPU / 16 GB is plenty (no GPU needed; inference is serverless on Token Factory). Open ports 80/443. Point a DNS A record (e.g. `forge.yourdomain.com`) at its public IP.

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
sudo mkdir -p /srv/forge && sudo chown 10001:10001 /srv/forge   # shared run workspaces (uid of the container user)
git clone <your public repo> forge && cd forge
cp .env.example .env && nano .env
```

Production `.env` essentials:
```
FORGE_ENV=production
FORGE_DOMAIN=forge.yourdomain.com
FORGE_PUBLIC_URL=https://forge.yourdomain.com
FORGE_CORS_ORIGINS=https://forge.yourdomain.com
FORGE_SESSION_SECRET=<openssl rand -hex 32>
DATABASE_URL=…               # step 2
NEBIUS_API_KEY=… NEBIUS_MODEL_NANO=… NEBIUS_MODEL_SUPER=… NEBIUS_MODEL_ULTRA=…
STORAGE_*=…                  # step 3
TAVILY_API_KEY=…             # optional: enables web research evidence
DOCKER_GID=$(getent group docker | cut -d: -f3)
```

Start:
```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
docker compose -f docker-compose.prod.yml logs -f api worker
```
`migrate` runs `alembic upgrade head` once before api/worker start. Caddy obtains a TLS certificate automatically.

Configuration is validated at startup: a missing/short session secret, a missing Nebius key or a non-HTTPS public URL in production stops the process with a readable message.

## 5. Smoke test
1. `https://forge.yourdomain.com/api/health` → `{"status":"ok","database":true}`
2. Sign up → **System health** shows a healthy worker; click **Live check**: the provider is reachable and lists models.
3. Create a project → connect `https://github.com/<public repo>` → New workflow (template) → Approve → Execute.

## Scaling and alternatives
* **More workers:** run `docker compose … up -d --scale worker=3` or start the backend image with `python -m forge.engine.worker` on other VMs (same `.env`). With more than one host, Object Storage (S3) is required: the local driver is single-host.
* **Nebius Serverless:** the API and worker are stateless containers and can run as Nebius Serverless Endpoints / Jobs pointing at the same Postgres + bucket (push the images to the Nebius container registry). The test sandbox needs a Docker daemon, so keep at least one VM-based worker for `TestRunner` or set `FORGE_SANDBOX_ENABLED=false` on serverless workers (tests then report `NOT_AVAILABLE`, honestly).

## Deployment checklist
| | Item |
|---|---|
| [ ] | Managed PostgreSQL created, `DATABASE_URL` set |
| [ ] | `alembic upgrade head` succeeded (`migrate` exited 0) |
| [ ] | web, api, worker containers healthy; Caddy has a certificate |
| [ ] | Object Storage bucket + keys configured |
| [ ] | `NEBIUS_API_KEY` + Nemotron model ids configured; System health → Live check OK |
| [ ] | Secrets only in `.env` on the VM (not in git) |
| [ ] | Sign-up / login works over HTTPS |
| [ ] | Repository upload + GitHub import work |
| [ ] | Compilation, execution, parallel branches, verification, retry, pause/resume, replay, real-time events verified with a real run |
