# Jailbreak Oracle

Adaptive LLM red-teaming & vulnerability discovery platform. Runs automated,
feedback-driven attack campaigns against target LLM endpoints, independently
verifies suspected jailbreaks, quantifies campaign spend, and ships analytics
plus reproducible benchmarks.

- **Backend**: FastAPI (Python 3.11), SQLAlchemy 2 async, Pydantic v2, Redis
  job queue + worker, PostgreSQL + pgvector (SQLite supported for dev/tests).
- **Frontend**: Next.js 16 / React 19 (dashboard, campaigns, reports, findings,
  mutation tree, budget panel).
- **Agents**: Attacker, Evaluator, Verifier (dual verification), Defender.
- **Governance**: per-campaign cost budgets with tripwires, rate limiting with
  trusted-proxy enforcement, role-based access control (admin/researcher),
  structured JSON logging with secret masking.

> **A note on purpose.** This project is an authorized red-teaming tool for
> evaluating your own models or models you have permission to test. Use it
> only on systems you own or are contracted to assess.

---

## Architecture

```
                        ┌──────────────────────────┐
                        │  frontend (Next.js 16)   │
                        │  auth · campaigns ·      │
                        │  reports · analytics     │
                        └────────────┬─────────────┘
                                     │ REST (Bearer JWT)
                                     ▼
   ┌───────────────────────────────────────────────────────────┐
   │                 FastAPI app (backend/app/main.py)         │
   │  SecurityHeaders · RateLimit · CORS · error handlers      │
   └───┬────────────┬──────────────┬──────────────┬────────────┘
       │            │              │              │
   auth router  campaigns router  reports router  analytics router
       │            │              │              │
       ▼            ▼              ▼              ▼
  core/auth.py  access.py    ReportService   MetricsService
  (bcrypt,JWT)  (ownership)                    + DefenderAgent
       │            │              │
       ▼            │              ▼
   RBAC deps   CampaignOrchestrator ───► VerifierAgent · EvaluatorAgent
       │        (services/campaign.py)          (dual verification)
       │            │
       ├─ AttackerAgent ──► MemoryService (pgvector | keyword fallback)
       ├─ ExecutionService ─► TargetProvider (LiteLLM / Mock)
       ├─ MutationEngine ──► lineage + dedup + budget
       └─ queue.py ──► Redis ──► worker.py (app/worker.py)

  config.py (pydantic-settings) ◄── .env
  session.py (async SQLAlchemy) ◄── SQLite dev | PostgreSQL+pgvector prod
```

## Quickstart (local, no Docker)

Prerequisites: Python 3.11, Node 20.

```bash
make setup                 # venv + backend deps + seed backend/.env
make api                   # backend on http://localhost:8000
```

In a second terminal:

```bash
cd frontend && npm install && npm run dev   # frontend on http://localhost:3000
```

Optional: run the durable campaign worker (requires Redis):

```bash
make worker                # Redis-backed campaign worker (python -m app.worker)
```

## Docker quickstart (full stack)

```bash
cp .env.example .env       # see note below
export SECRET_KEY="$(python -c "import secrets; print(secrets.token_urlsafe(64))")"
docker compose up --build -d
docker compose run --rm backend alembic upgrade head   # create schema
```

This brings up `db` (pgvector), `redis`, `backend` (:8000), `worker`, and
`frontend` (:3000). The frontend is configured to call
`http://localhost:8000/api/v1`.

A repo-root `.env` file is consumed by `docker compose`; the only mandatory
variable is `SECRET_KEY` (the compose file fails loudly if it's missing).
LLM API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`) and
`BOOTSTRAP_ADMIN_EMAILS` are optional but required to use real providers.

> **Postgres vs SQLite.** The containerized stack uses PostgreSQL + pgvector
> for real embedding retrieval. Bare-metal dev uses SQLite with an honest
> keyword-retrieval fallback (no fake embeddable tiers).

## Configuration

Copy `backend/.env.example` to `backend/.env` and edit. Key settings:

| Variable | Meaning |
|---|---|
| `APP_ENV` | `development` or `production`. Production requires `SECRET_KEY`, PostgreSQL, `REDIS_URL`. |
| `ASYNC_DATABASE_URL` | SQLAlchemy async URL. `sqlite+aiosqlite:///./oracle.db` (dev) or `postgresql+asyncpg://...` (prod). |
| `SYNC_DATABASE_URL` | Symmetric sync URL used by Alembic (psycopg2 for Postgres). |
| `ATTACKER_PROVIDER` / `ATTACKER_MODEL` | Attacker agent. `litellm` for real LLMs, `mock` for deterministic runs. |
| `EVALUATOR/VERIFIER/DEFENDER_PROVIDER(MODEL)` | The three judging agents. |
| `DEFAULT_TARGET_PROVIDER` / `DEFAULT_TARGET_MODEL` | Campaign target default (per-campaign override supported). |
| `EMBEDDING_PROVIDER` | `openai` (pgvector path) or `mock`. |
| `MAX_CAMPAIGN_COST` | Global USD cap enforced per campaign (budget tripwire). |
| `TRUSTED_PROXIES` | Comma-separated proxy IPs/CIDRs trusted to set `X-Forwarded-For` for rate limiting. Empty disables XFF trust. |
| `BOOTSTRAP_ADMIN_EMAILS` | Emails promoted to `admin` on registration (admin user-management API). |
| `REDIS_URL` | Redis connection for the queue worker + distributed rate limiting (required in production). |

## Development commands

Run from the repo root (`make` reads the `Makefile`):

```bash
make setup       # venv + deps + .env
make test        # pytest (isolated temp SQLite DB; never touches dev/prod data)
make lint        # flake8 (app + tests + benchmarks)
make typecheck   # mypy (app + benchmarks)
make check       # lint + typecheck + test + benchmark  (== CI entrypoint)
make benchmark   # reproducible benchmark -> benchmark-report.json
make api         # uvicorn dev server with reload
make worker      # Redis-backed campaign worker
make migrate     # alembic upgrade head
make compose-up  # full stack via docker compose
```

## API surface (`/api/v1`)

- `auth`: `POST /auth/register`, `POST /auth/login`, `GET /auth/me`,
  admin-only `GET /auth/users`, `PATCH /auth/users/{id}/role`.
- `campaigns`: create/start/list/detail, `POST /campaigns/setup-demo`,
  `GET /campaigns/{id}/attacks`.
- `reports`: per-experiment report, `POST /reports/experiment/{id}/defense`.
- `vulnerabilities`: list/confirm.
- `analytics`: `GET /analytics/metrics` (user-scoped aggregates),
  `GET /analytics/experiments/{id}/metrics`.

All data routes require a Bearer JWT; ownership is enforced per user project
(403 on cross-owner access). Snapshot the full schema via `GET /openapi.json`.

## Testing & CI

- Isolated tests: the suite redirects to a temporary SQLite DB in
  `backend/tests/conftest.py` and never touches the dev/production database.
  Set `TEST_DATABASE_URL` for a dedicated Postgres test instance if desired.
- CI (`.github/workflows/ci.yml`) runs backend lint + typecheck + tests +
  benchmark gate, and frontend lint + build. `make check` reproduces the
  backend job locally.

## Project layout

```
backend/app/            FastAPI application package (main.py, api/, core/, db/,
                        models/, schemas/, agents/, strategies/, services/, targets/)
backend/alembic/        Migrations (Postgres-safe; SQLite-friendly env guards)
backend/benchmarks/     Pure metric math + reproducible benchmark harness
backend/tests/          25+ test modules (152+ tests)
frontend/               Next.js 16 application
docker-compose.yml      Full stack (db, redis, backend, worker, frontend)
Makefile                Developer task runner
docs/AUDIT.md           Audit + phase-log (E-01 … E-25 tracked here)
```

## Governance & observability

- **Cost**: every LLM call (attacker, target, evaluator, verifier) is tracked
  into a per-campaign ledger (`token_usage` table); budgets are checked before
  each call and campaigns abort cleanly when a tripwire fires.
- **Confidence**: suspected jailbreaks pass through an independent verifier
  opinion before being recorded as confirmed vulnerabilities.
- **Logging**: structured JSON lines with a strict context-field whitelist and
  secret redaction (`api_key`, `Bearer` tokens, JWTs, `sk-...`, ...). Provider
  errors and `llm_call` records are structured events; campaign-scoped errors
  carry the `campaign_id`.