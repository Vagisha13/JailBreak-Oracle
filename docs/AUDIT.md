# Jailbreak Oracle — Repository Audit (Phase 0)

Audit date: 2026-09-06. This document reflects the **working tree as it exists now**
(uncommitted changes included), not the stale initial commits.

Test baseline recorded during recon: **60/60 tests pass** with the repo-root `.venv`
(`backend/tests`). The committed `backend/venv` is **broken** (missing
`python-multipart`), so a fresh clone using the documented env cannot run the suite.

---

## 1. Directory Structure

| Path | Purpose |
|---|---|
| `backend/app/` | Canonical FastAPI application package |
| `backend/app/main.py` | Canonical API entrypoint (lifespan, middleware, routers) |
| `backend/app/worker.py` | Redis-backed campaign worker entrypoint (`python -m app.worker`) |
| `backend/app/core/` | Config, auth, errors, logging, ratelimit, security middleware |
| `backend/app/db/` | SQLAlchemy async engine/session + metadata base |
| `backend/app/models/` | ORM models (all in `domain.py`, re-exported) |
| `backend/app/schemas/` | Pydantic request/response/internal schemas |
| `backend/app/api/` | Routers (`campaigns`, `reports`, `vulnerabilities`, `auth`), `deps.py`, `access.py` |
| `backend/app/agents/` | Attacker / Evaluator / Verifier LLM wrappers |
| `backend/app/strategies/` | Attack family templates + registry (7 strategies) |
| `backend/app/services/` | Campaign orchestration, execution, evaluation, verification, memory, heuristics, report, queue |
| `backend/app/targets/` | Provider abstraction (LiteLLM, Mock), factory, embeddings |
| `backend/alembic/` | Migration environment + single initial migration |
| `backend/tests/` | 19 test modules, 60 passing tests |
| `backend/main.py` | **Duplicate** minimal fallback API entrypoint (deprecated) |
| `frontend/` | Next.js 16.3 / React 19 application |
| `frontend/src/` | Pages, `lib` (api/auth/types), `components` |
| `docker-compose.yml` | Postgres (pgvector) + Redis only — **no app/worker/frontend services** |
| `task.md` | Implementation tracker (claims several capabilities the code does not yet have) |
| `docs/` | This audit + architecture/limitations docs (created in this effort) |
| `.venv/` | Working Python environment used to run tests |
| `backend/venv/` | Broken committed environment (missing `python-multipart`) |

## 2. Entrypoints

| Entrypoint | Status | Notes |
|---|---|---|
| `backend/app/main.py` | **Canonical** | `uvicorn app.main:app`. Lifespan calls `create_all` (see issue E-03). |
| `backend/app/worker.py` | Canonical | `python -m app.worker`. Redis queue worker with recovery. |
| `backend/main.py` | **Duplicate / deprecated** | Minimal health-only app; still imported by `backend/tests/test_health.py`. Keep as deprecated alias, do not run. |
| `alembic` | Migrations | Single migration, drifted vs ORM (E-04). |
| `frontend` | Next.js | `npm run dev` / `next build`. |

## 3. Issue Table

Severity: **Critical** (data loss/security/schema corruption) · **High** (behavioral gap vs claims) · **Medium** (hygiene/scale) · **Low** (cosmetic).

| # | Component | Issue | Severity | Proposed Fix |
|---|---|---|---|---|
| E-01 | `backend/tests/conftest.py` | Test suite runs against the **development database** (`ASYNC_DATABASE_URL` default `oracle.db`) via `create_all`/`drop_all` — running `pytest` destroys dev data. | Critical | Introduce `TEST_DATABASE_URL`; point the engine at an isolated temp file before importing app modules; only ever create/drop the test DB. |
| E-02 | `backend/venv` | Broken environment: missing `python-multipart`; pytest fails collection. Contradicts one-command setup. | High | Delete the committed venv (gitignored anyway) and document a single `python -m venv` + `pip install -r requirements.txt`. |
| E-03 | `backend/app/main.py` | `Base.metadata.create_all` runs in the production startup lifespan — migrations are not the schema mechanism in prod. | High | Restrict `create_all` to development/tests; in production verify `alembic_version` exists and fail loudly otherwise. |
| E-04 | `backend/alembic/` | Architecture drift: `agent_runs` (migration: `agent_role/input_summary/output_summary/latency_ms` vs ORM: `agent_type/state_json`) and `attack_mutations` (migration: `parent_attack_id/child_attack_id/generation/score` vs ORM: `attack_id/mutation_type/mutated_prompt`) differ. Orphan tables exist with **no models**: `analytics_reports`, `model_calls`, `defenses`, `regression_tests`, `defense_tests`. | Critical | Regenerate initial migration from ORM metadata; PG-extension guard in `env.py` (currently `CREATE EXTENSION vector` will break with fatal error on SQLite); drop orphan tables after confirming zero runtime/ORM references. |
| E-05 | `backend/app/api/deps.py` vs `routers/campaigns.py` | Verifier is wired into the orchestrator **only via `deps.py`** (used by the worker) — the in-process fallback path in `campaigns.py` builds an orchestrator **without a verifier**, so half the execution paths never run dual verification. | High | Single orchestrator factory (with optional verifier) shared by router + worker. |
| E-06 | `backend/app/services/campaign.py` | Orchestrator is a **loop wrapper**: strategy chosen by `random.random()`; "exploit" branch is also random; no feedback-driven planning; relies on in-memory state only; cannot resume after worker restart. | High | Stateful orchestrator reading/writing DB state per round (Phase 2). |
| E-07 | `backend/app/models/domain.py` (`AttackMutation`) | Mutation hints are **written but never read** — the adaptive loop is cosmetic. | High | Real mutation engine consuming evaluator/verifier results with lineage + dedup (Phase 3). |
| E-08 | `backend/app/strategies/adaptive_mutation.py`, `multi_turn.py` | Both are single-turn prompt templates; no feedback input, no multi-turn state, no follow-up turns. Claims "adaptive"/"multi-turn" falsely. | High | Real mutation feedback + persisted conversation state (Phases 3–4). |
| E-09 | `backend/app/agents/verifier.py` | Verifier prompt includes the **evaluator's reasoning** as input — not independent; single conjunction. No agreement logic; verifier result not persisted to the attack record. | High | Independent verifier input; structured confirmed/refuted/inconclusive; orchestrator computes agreement; persist verifier fields on attack (Phase 6). |
| E-10 | `backend/app/agents/evaluator.py` | Evaluator output lacks `evidence`, `false_positive_indicators`, and an explicit `ambiguous` result; confidence/severity unconstrained (no `Literal`/`ge`/`le`). | High | Structured EvaluatorResult with constrained enums (Phase 6). |
| E-11 | `backend/app/services/report.py:117` | Remediation summary is **hardcoded** 3 strings — presented as "AI defense guidance". No Defender agent, no regression validation. | High | Duty-cycle Defender agent generating recommendations + regression scores + `defense-report` endpoint (Phase 7). |
| E-12 | Cost governance | `MAX_CAMPAIGN_COST` (config) is never enforced; token/cost tracked **only** for target calls (`AttackResult.token_usage_json`); attacker/evaluator/verifier usage discarded; no per-model cost table; budget checks absent. A 500-round campaign is unbounded spend with no tripwire. | High | TokenTracker + CampaignBudget + budget-before-call enforcement (Phase 8). |
| E-13 | `backend/app/services/memory.py` | SQLite fallback is **recency-based** ("last N attacks"), silently presented as retrieval. No warning; no successful/failed/strategy-specific retrieval; embeddings never stored on SQLite. | Medium | Explicit `AttackMemory` interface; honest SQLite fallback with startup warning; pgvector path uses `<=>`; add successful/failed/strategy/lineage retrievals (Phase 5). |
| E-14 | `backend/app/models/domain.py` (`AgentRun`) | Model defined, **never instantiated** anywhere — dead code. | Medium | Remove or wire into orchestrator telemetry; migrate table to match. |
| E-15 | Indexes | `experiments.project_id` (used for user isolation lookups) is not indexed; only `experiments.status` is. `attacks.experiment_id`/`category` and `attack_results.attack_id` and related FKs exist. | Medium | Add `experiments.project_id` index (+ `attacks.parent_attack_id`/`round_number` after Phase 3). |
| E-16 | Config | Defined-but-unused values: `MAX_CAMPAIGN_COST`, `DEFAULT_MAX_ROUNDS`, `DEFAULT_ATTACK_BUDGET`, `LLM_RATE_LIMIT_PER_MINUTE` (bucket is used, value unused is fine). `get_current_user_optional`, `experiment_belongs_to_user` (access.py) unused. | Low | Document all env vars in `.env.example`; enforce budget settings; remove or keep documented. |
| E-17 | Onboarding infra | No `.env.example`, no `Makefile`/`justfile`, no CI workflow, no Dockerfiles, no README at repo root; `docker-compose.yml` omits backend/worker/frontend. | High | Add all phase-1 artifacts (env template, Makefile, CI, Docker build), full compose stack + README (Phase 13). |
| E-18 | `backend/main.py` duplicate | Minimal fallback entrypoint still present and referenced by `test_health.py`. | Low | Convert to a deprecated alias of `app.main`; update test to canonical app. |
| E-19 | Dependencies | `black` listed but unused (flake8 is the active linter); no type checker; `greenlet` required for async SQLAlchemy (keep); `psycopg2-binary` needed for alembic-on-PG (keep). | Low | Remove `black`, add `mypy`, keep the rest pinned. |
| E-20 | Observability | No structured `llm_call` / `attack_executed` log records (some JSON events exist); masking covers only 3 patterns; no `campaign_id` on provider error logs. | Medium | Add structured LLM-call and attack-execution log events; extend masking (Phase 13). |
| E-21 | Frontend | No mutation-tree view, no budget/token display, no confirmed-findings list, no analytics charts; `reports/[id]` depends on generated report only. | Medium | Mutation tree + budget header + findings + charts backed by DB endpoints (Phase 11). |
| E-22 | Benchmarking | No benchmark framework, no metrics endpoint (`/analytics/metrics`). | Medium | `backend/benchmarks/` + metrics service + endpoint (Phase 12). |
| E-23 | `backend/app/main.py` + `errors.py` | Good global handlers exist; `RequestValidationError` handler returns raw Pydantic errors (safe). No rate-limit set on `/setup-demo`. | Low | Leave; optional tightening. |
| E-24 | `settings.RATE_LIMIT` | Rate limiter uses `x-forwarded-for` trust without verifying proxy; acceptable for demo, note in production caveats. | Low | Document; production behind trusted proxy. |
| E-25 | Worker semantics | `process_campaign_job` marks FAILED on any exception, but `run_campaign` itself already marks FAILED (consistent). PENDING re-queue exists. Test coverage for worker/recovery is thin. | Medium | Add worker/recovery tests (Phase 9). |

## 4. Dependency Graph (major components)

```
                       ┌──────────────────────────┐
                       │  frontend (Next.js 16)   │
                       │  auth · campaigns ·      │
                       │  reports · components    │
                       └────────────┬─────────────┘
                                    │ REST (Bearer JWT)
                                    ▼
   ┌───────────────────────────────────────────────────────────┐
   │                 FastAPI app (app/main.py)                 │
   │  SecurityHeaders · RateLimit · CORS · error handlers      │
   └───┬────────────┬──────────────┬──────────────┬────────────┘
       │            │              │              │
   auth router  campaigns router  reports router vulner. router
       │            │              │              │
       ▼            ▼              ▼              ▼
  core/auth.py  access.py    ReportService   VerificationService
  (bcrypt,JWT)  (ownership)
       │            │              │              │
       │            ▼              │              ▼
       │   CampaignOrchestrator ───┼────────► VerifierAgent
       │   (services/campaign.py)  │              │
       │            │              │         targets/factory
       │            ├─ AttackerAgent ──► MemoryService (pgvector | SQLite)
       │            ├─ ExecutionService ─► TargetProvider (LiteLLM/Mock)
       │            ├─ EvaluationService ─► HeuristicEngine → EvaluatorAgent
       │            └─ queue.py ──► Redis ──► worker.py (app/worker.py)
       │
  config.py (pydantic-settings) ◄── .env
  session.py (async SQLAlchemy) ◄── SQLite dev | PostgreSQL+pgvector prod
```

## 5. Canonical Entrypoints (use these)

1. **API**: `uvicorn app.main:app` (run from `backend/`).
2. **Worker**: `python -m app.worker` (run from `backend/`).
3. **Migrations**: `alembic upgrade head` (from `backend/`; PostgreSQL in production).
4. **Frontend**: `npm run dev` (from `frontend/`).
5. **Tests**: `pytest` (from `backend/`, root `.venv`).

`backend/main.py` is a deprecated fallback and must not be used.

## 6. Working Functionality to Preserve

- Auth: `/auth/register|login|me` with bcrypt + JWT (`core/auth.py`), enforced on all data routes.
- Ownership enforcement at the API layer (`api/access.py`: 403 isolation across projects/targets/experiments/vulnerabilities).
- Rate limiting middleware with in-memory + Redis backends.
- Redis job queue + worker with stale-campaign recovery (`services/queue.py`, `worker.py`).
- LiteLLM provider: timeout, retry with exponential backoff, error normalization, token usage on target responses.
- Mock provider + mock embeddings (deterministic, CI-safe).
- Heuristic refusal short-circuit (saves API calls).
- Strategy registry + 7 registered strategies.
- Memory service (pgvector `<=>` path for PostgreSQL; fallback for SQLite).
- Report service (severity/strategy analytics, risk score).
- Global error handlers, security headers, structured JSON logging with masking.
- Frontend pages: login, dashboard, campaigns list/new/detail, reports list/detail; components (AppShell, StatusBadge, Collapsible, Empty/Error states, Providers, LoadingSpinner).
- 60 passing tests.

## 7. Dead Code Candidates (remove after confirmation)

| Candidate | Evidence | Action |
|---|---|---|
| `backend/main.py` | Duplicate entrypoint; only `test_health.py` uses it | Convert to deprecated alias of `app.main`; keep health test on canonical app |
| `AgentRun` model | Never instantiated (grep-verified) | Remove ORM model + regenerate migration; or wire into Phase-2 round telemetry |
| `attack_mutations` write path | Written at `services/campaign.py:231`, never read | Repurpose in Phase 3 (lineage + dedup) |
| Orphan tables: `analytics_reports`, `model_calls`, `defenses`, `regression_tests`, `defense_tests` | No ORM models reference them | Drop from regenerated migration (recreate properly in Phases 6–7 when real features land) |
| `settings.MAX_CAMPAIGN_COST`, `DEFAULT_MAX_ROUNDS`, `DEFAULT_ATTACK_BUDGET` | Never read by code | Keep (used by Phase 8) or document |
| `api/access.py: experiment_belongs_to_user`, `auth.get_current_user_optional` | Unused | Remove or document as conveniences |