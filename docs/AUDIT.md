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

---

## 8. Phase 1 — Foundation & CI (2026-09-06)

Resolved the onboarding, safety and tooling issues from Phase 0. **60/60 tests still pass.**

| Phase 0 issue | Resolution |
|---|---|
| E-01 (Critical) | `backend/tests/conftest.py` now redirects the engine to an **isolated temp SQLite DB** before any app module is imported. Honors `TEST_DATABASE_URL` when set (e.g. dedicated PG test instance in CI). Dev `oracle.db` is never touched. |
| E-03 (High) | `app/main.py` lifespan: `create_all` runs **only** in non-production environments. Production verifies the schema exists (via `inspect.has_table("experiments")`) and raises a clear error telling the operator to run `alembic upgrade head`. |
| E-04 (Critical) | `alembic/env.py` now guards `CREATE EXTENSION vector` behind `dialect.name == "postgresql"` so migrations run cleanly on SQLite and PostgreSQL. Migration rewrite (ORM-synced + orphan-table cleanup) is staged in the same commit defining Phase 2. |
| E-18 (Low) | `backend/main.py` is now a thin deprecated alias of `app.main` (emits `DeprecationWarning`); `tests/test_health.py` targets the canonical app. |
| E-19 (Low) | Swapped `black` for `mypy==1.9.0` in `requirements.txt`; added `backend/.flake8` (120-col) and `backend/mypy.ini`; fixed **every** flake8 finding (unused imports, unused locals, missing newline-at-EOF, an `F811` shadow) and **all 7** mypy findings (implicit `Optional`, untyped engine kwargs, `object`-typed Redis client, reassignment typing). |
| E-17 (High) | Root `Makefile` (setup/test/lint/typecheck/check/api/worker/migrate/compose targets) and `.github/workflows/ci.yml` (backend lint + typecheck + tests on Ubuntu/Python 3.11; frontend lint + build on Node 20). |
| E-02 (High) | Documented one-command setup via `make setup`; committed `backend/venv` remains broken and is gitignored — see `Makefile`. |

New tooling commands (run from repo root): `make lint`, `make typecheck`, `make check` (lint+typecheck+test), `make test`.
New `make check` is the exact CI entrypoint for backend so local == CI.

Open items carried into Phase 2: regenerated ORM-synced migration + orphan-table drop, verifier unification (E-05), and the full docker-compose app stack. Root `README.md` deliberately deferred past Phase 1 (E-17 partial).

---

## 9. Phase 2 — Schema landing & verifier unification (2026-09-06)

**64/64 tests pass.** Lint + typecheck gates green.

| Phase 0 issue | Resolution |
|---|---|
| E-04 (Critical) | ORM-synced initial migration verified against a fresh SQLite DB: table set matches `Base.metadata` exactly (9 business tables; only `alembic_version` extra), all columns present, no orphan tables (`analytics_reports`, `model_calls`, `defenses`, `regression_tests`, `defense_tests` gone). `upgrade`→`downgrade base`→`upgrade` round-trip verified. `agent_runs` (`agent_type/state_json`) and `attack_mutations` (`attack_id/mutation_type/mutated_prompt`) now match the ORM. |
| E-05 (High) | New `app/services/factory.py::build_campaign_orchestrator()` is the single composition root. Router (`campaigns.py:get_orchestrator`), worker (`worker.py:build_orchestrator`), and `api/deps.py` all delegate to it; the router's in-process fallback now wires a verifier, so **dual verification runs on every execution path**. Regression tests in `tests/test_factory.py` lock this in. |

Migration check command (used during Phase 2 verification):
```
cd backend && ASYNC=... SYNC=sqlite:///./_migration_check.db alembic upgrade head
```

Still open (intentional): docker-compose app stack (`backend`/`worker`/`frontend` services + README) — scheduled with the container-phase; `AgentRun` telemetry wiring; budget enforcement (Phase 8).

---

## 10. Phase 3 — Feedback-driven mutation engine (2026-09-06)

**69/69 tests pass.** Lint + typecheck gates green.

| Phase 0 issue | Resolution |
|---|---|
| E-07 (High) | Mutation hints were written but never read. New `app/agents/attacker.py::generate_mutated_attack` + `app/services/mutation.py::MutationEngine` consume evaluator feedback (category/reasoning/severity/confidence), optionally the target's actual response and verifier result, and generate a genuinely evolved prompt. |
| E-06 (High) | Orchestrator PLAN is now feedback-driven (`strategy_scores` + `_select_strategy`: explore untried / exploit best-scoring). The MUTATE step actually executes: blocked attempts on mutation-capable strategies trigger a retry against an evolved prompt (bounded to `attack_budget // 2`), eliminating the old `strategy_shift` hint block. |
| E-08 (High) | `AdaptiveMutationStrategy` and `MultiTurnStrategy` now implement `supports_mutation` + `get_mutation_prompt()`. Adaptive mutation rewrites the prompt using evaluator reasoning; multi-turn produces the genuine NEXT turn escalated against the target's prior response. Static strategies remain single-shot. |
| Lineage + dedup | `attacks` gains `parent_attack_id` (self-FK) and `round_number` via migration `c3e1a9d8b2f7_attack_lineage` (SQLite-safe batch mode; upgrade/downgrade round-trip verified). Every mutation rows an `attack_mutations` record (`mutation_type`, `mutated_prompt`). Dedup: normalized-prompt set over the experiment's recent 50 attacks skips near-identical repeats so budget isn't burned re-firing the same payload. |
| API | `/campaigns/{id}/attacks` now returns `parent_attack_id`, `round_number`, and `mutation_type`. |

New tests: `tests/test_mutation.py` (5) — lineage persistence, dedup, adaptive/multi-turn prompt construction, and a full campaign-loop RETRY integration test (blocked primary → feedback mutation → confirmed jailbreak).

Architecture notes: orchestrator stores `MutationEngine` internally (from the attacker agent) so both router and worker paths get it automatically; verifier feedback is wired into `AttackFeedback.verifier_result` for future false-positive steering.

---

## 11. Phase 4 — Multi-turn conversation state + resumable campaigns (2026-09-06)

**73/73 tests pass.** Lint + typecheck gates green.

| Area | Resolution |
|---|---|
| Multi-turn conversation state (E-08 remainder) | `AttackerAgent._load_conversation_history` walks the `parent_attack_id` lineage oldest→newest and attaches each turn's latest target response. `AttackStrategy.get_mutation_prompt` gained `conversation_history`; `MultiTurnStrategy` now renders a full "CONVERSATION SO FAR" transcript across >2 turns instead of only the last exchange, and `AdaptiveMutationStrategy` weighs lineage depth (`n turn(s) deep`) into its mutation directives. Root attacks now stamp `round_number` (`generate_and_persist_attack(round_number=...)`). |
| DB-backed round state / resume (E-06 remainder) | `CampaignOrchestrator.run_campaign` hydrates progress via `_load_progress`: starting round from `max(round_number)`, plus `strategies_used` and per-strategy jailbreak scores recomputed from persisted attacks/vulnerabilities. A re-run continues from round N+1 instead of replaying round 1 (verified: 2-round run + 5-round resume yields exactly rounds 1..5, no duplicates). |
| Live model (E-14) | `AgentRun` is no longer dead code: the orchestrator persists per-round telemetry rows for `attacker`, `evaluator` (round/verdict/severity/category/attack_id), `verifier` (confirmed status from `VerificationResult`), and a final `campaign` round-state row. Best-effort: telemetry failures never abort the campaign. |
| Worker restart recovery | `app/worker.py::recover_stale_campaigns` now reverts stale RUNNING experiments to PENDING and re-queues them (key `resumed_running`), so a worker crash resumes a campaign from its DB round state instead of permanently failing it. Resumed IDs are excluded from the "very old PENDING → FAILED" sweep on the same pass. |

New tests: `tests/test_stateful_campaign.py` (4) — full conversation-history chaining, adaptive-mutation turn-depth, cross-run resume continuity, and `AgentRun` telemetry shape. Updated `tests/test_campaign_lifecycle.py` for the RUNNING→PENDING resume semantics.

---

## 12. Phase 5 — Honest memory & retrieval filters (2026-09-06)

**79/79 tests pass.** Lint + typecheck gates green.

| Issue | Resolution |
|---|---|
| E-13 | `app/services/memory.py` now exposes the explicit `AttackMemory` interface with two backend implementations. `VectorMemoryService` keeps the pgvector `<=>` path; `KeywordMemoryService` is the **honest** SQLite fallback — it logs a startup warning and ranks attacks by Jaccard query-token overlap instead of silently presenting "last N attacks" as retrieval. Zero-overlap queries return empty results rather than recency soup, and `embed_attack` is a documented no-op (no embeddings faked/persisted without pgvector). `MemoryService` remains as a facade over a cached `is_pgvector_available()` probe, preserving all call sites. |
| Retrieval filters | `retrieve_similar_attacks` gained `status` (`"successful"` = has recorded vulnerability / `"failed"`) and `strategy_name` filters in both backends; `MemoryService.dispatcher`/vector SQL build WHERE clauses accordingly; invalid status values raise `ValueError`. |
| RAG wiring | Attacker RAG is now scoped to the active campaign (`experiment_id`). `generate_mutated_attack` additionally retrieves up to 2 *successful* same-strategy prior lines and appends them as "SUCCESSFUL PRIOR LINES" learning context so mutations build on what already landed. |

New tests: `tests/test_memory_honest.py` (6) — relevance-not-recency ranking, honest empty result on zero overlap, status filters (successful/failed), strategy filter + invalid-status rejection, facade SQLite resolution, and mutation-time successful-prior-lines context.

---

## 13. Phase 6 — Structured evaluator/verifier outputs (2026-09-06)

**87/87 tests pass.** Lint + typecheck gates green.

| Issue | Resolution |
|---|---|
| E-10 | `EvaluatorVerdict` is now structured: `verdict` is a `VerdictType` enum (`JAILBREAK`/`AMBIGUOUS`/`BLOCKED`), `severity` is `Literal`-constrained, `confidence` is `ge=0.0/le=1.0`, and fields `evidence` + `false_positive_indicators` are collected. `is_jailbreak` is retained as a derived convenience kept consistent by a `model_validator` (either input shape works). Evaluator prompt now requests the structured schema incl. `AMBIGUOUS`. |
| E-09 independence | `VerifierAgent.verify` no longer accepts evaluator reasoning — the second opinion sees only the raw attack prompt + target response. Structured `VerificationDisposition` (`CONFIRMED`/`REFUTED`/`INCONCLUSIVE`) with `verdict.confidence` and `evidence`; `is_confirmed` kept as back-compat derived flag. |
| E-09 persistence | Migration `d4e2b0c9a3g8_verifier_fields` adds `verification_reasoning`, `remediation_guidance`, `verifier_confidence`, `verified_at` to `vulnerabilities` (SQLite-safe batch; upgrade/downgrade round-trip + schema parity verified). `VerificationService` persists all verifier fields and maps `CONFIRMED→CONFIRMED_VULNERABILITY`, `REFUTED→FALSE_POSITIVE`, `INCONCLUSIVE→INCONCLUSIVE` — evaluator reasoning is no longer mutated. |
| Agreement | Orchestrator records evaluator↔verifier agreement in verifier `AgentRun` telemetry (`verified_status` + `agreement`) and emits a structured `verification.agreement` event on every confirmed path (primary + mutated). |

New tests: `tests/test_structured_outputs.py` (8) — verdict derivation both ways, `AMBIGUOUS` semantics, severity/confidence constraint rejection, structured evaluator prompt, verifier independence (prompt must not contain evaluator reasoning), disposition→status mapping, persisted verifier fields, and `INCONCLUSIVE` persistence. Migration round-trip verified on a fresh SQLite DB.

---

## 14. Phase 7 — Defender agent, remediation & regression scores (2026-09-06)

**99/99 tests pass.** Lint + typecheck gates green.

| Issue | Resolution |
|---|---|
| E-11 (hardcoded remediation) | `ReportService` no longer emits the 3 canned strings. The base report's `remediation_summary` is now **evidence-derived** from the actual findings (`_derive_remediation_summary`: per-category lines sorted by top severity, noting verified count; honest "no findings" message when empty). |
| E-11 (no Defender agent) | New `DefenderAgent` (`app/agents/defender.py`) duty-cycles on campaign findings. `DefenderVerdict` is strictly typed: `recommendations` (min 1), `regression_score` (`ge=0.0/le=100.0`, predicted probability confirmed attack classes still succeed after mitigation), `overall_assessment`, and an honesty flag `is_fallback`. The LLM prompt presents severity breakdown, findings, and observed strategies and demands schema-conformant JSON. |
| E-11 (honest AI vs fallback) | When no provider is configured, the provider errors, or the output is unparseable/out-of-range, the agent degrades to a documented deterministic fallback: recommendations built from the **verifier's own `remediation_guidance`** (deduped, highest severity/confidence first) plus category-derived lines, and a weighted `regression_score` (CRITICAL 40 · HIGH 25 · MEDIUM 12 · LOW 5 × confidence, capped 100). `is_fallback=True` is surfaced in the API response and a `defender.fallback` structured event is logged — the client is never presented with LLM output that was actually rule-based. |
| E-11 (no endpoint) | `POST /api/v1/reports/experiment/{id}/defense` returns a structured `DefenseReport` (recommendations, regression score, assessment, `is_fallback`) after owner 403/404 checks. Wired via `DEFENDER_PROVIDER`/`DEFENDER_MODEL` settings through the shared `TargetFactory` (mock-safe in CI via the tests' provider fixture). |

New tests: `tests/test_defender.py` (12) — LLM JSON parsing, fallback on no-provider/provider-error/invalid-JSON/out-of-range-regression, empty-context handling, weighted regression score math (CRITICAL 40·1.0 + HIGH 25·0.9 = 62.5), service-level LLM + fallback paths (verifier guidance surfaced), unknown-experiment 404, API 200 with dependency override, and cross-owner 403.

---

## 15. Phase 8 — Cost governance (TokenTracker + budget tripwires) (2026-09-06)

**111/111 tests pass.** Lint + typecheck gates green.

| Issue | Resolution |
|---|---|
| E-12 (per-model cost table) | New `app/services/budget.py` ships an explicit `MODEL_COST_USD_PER_1K` table (gpt-4o-mini/4o/4-turbo/4, claude-3-5-sonnet/opus/haiku, deepseek-chat/reasoner, `ollama` = $0) with longest-first substring resolution and a documented conservative default for unknown models. `estimate_cost_usd(model, prompt_tokens, completion_tokens)` derives USD spend from every call. |
| E-12 (tracking discarded usage) | `TokenTracker` now records **every** LLM call — attacker, target, evaluator, verifier — not just target calls. `BudgetedTargetProvider` is a transparent `TargetProvider` wrapper the orchestrator installs around all four providers per run (stored core providers prevent wrapper-stacking across resumed runs). Token counts flow from `TargetResponse` on all paths. |
| E-12 (budget before call) | `CampaignBudget.check_allow()` trips **before** each LLM call using a conservative estimate (prompt chars/4 + 1024 completion-token reserve); `BudgetExceededError` aborts the campaign, marking it FAILED with a `campaign.budget_exceeded` event and a final `campaign` AgentRun containing the reason + budget telemetry. `MAX_CAMPAIGN_COST` is now enforced via `CampaignConfig.max_cost_usd` (set by `_build_config`, default `settings.MAX_CAMPAIGN_COST`). |
| E-12 (no persistence / resume) | Migration `e5f1d2c4a7b9_token_usage` adds the `token_usage` ledger (role, model, tokens, cost_usd, FK→experiments) — round-trip + schema parity verified on fresh SQLite. Each call persists best-effort (`_persist_usage_entry`, failures never abort a call); `_load_campaign_spend` seeds a resumed campaign's tracker so the budget survives worker restarts instead of restarting at zero. Target `AttackResult.token_usage_json` now also records `model`/`cost_usd`/`role`. |

New tests: `tests/test_budget.py` (12) — cost-table resolution (specific, substring, ollama, unknown default), cost math, per-role tracking, seeded resume spend, pre-call tripwire, remaining-budget floor, wrapper recording+persistence, wrapper block-before-delegate on exceeded budget, config-model override, campaign ledger persistence + reported cost, resume-not-double-counting, and FAILED-on-budget-exceeded lifecycle.

## 16. Phase 9 — Worker hardening: liveness heartbeat + recovery semantics (2026-09-06)

**115/115 tests pass.** Lint + typecheck gates green. Migration round-trip verified on fresh SQLite.

| Issue | Resolution |
|---|---|
| E-25 (staleness keyed on `created_at`) | Recovery swept any RUNNING campaign older than the timeout — a campaign legitimately running for hours was killed mid-flight. New `experiments.heartbeat_at` (migration `f6a2e3d5b8c0`) is a worker liveness heartbeat bumped by the orchestrator on every status write and every round, and by `process_campaign_job` up-front on dequeue. `_resume_stale_running` now judges staleness on the heartbeat; legacy NULL-heartbeat rows fall back to `created_at`. |
| E-25 (thin worker test coverage) | Added heartbeat-focused recovery tests: fresh-heartbeat RUNNING survives recovery, stale-heartbeat RUNNING is resumed, legacy-NULL-heartbeat falls back to `created_at` (old swept / fresh kept), and heartbeat is observed non-NULL mid-run and after completion. Stack now 115 tests. |

New tests in `tests/test_campaign_lifecycle.py` (4, E-25): `test_running_with_fresh_heartbeat_not_swept`, `test_running_with_stale_heartbeat_resumed`, `test_legacy_running_without_heartbeat_uses_created_at`, `test_heartbeat_bumped_during_and_after_campaign`.

## 17. Phase 10 — Role-based authorization + trusted-proxy rate limiting (2026-09-06)

**129/129 tests pass.** Lint + typecheck gates green.

| Issue | Resolution |
|---|---|
| E-24 (XFF spoof bypass) | The rate limiter trusted `X-Forwarded-For` unconditionally, so any client could rotate the header to mint fresh per-IP buckets and bypass limits. New `settings.TRUSTED_PROXIES` (comma-separated IPs/CIDRs, default empty) gates XFF trust: a forwarded header is honored only when the direct socket peer is a configured proxy (`_is_trusted_proxy` via `ipaddress`, malformed entries skipped); otherwise the real peer is used. |
| E-16 (dead auth helpers) | Removed unused `auth.get_current_user_optional` and `access.experiment_belongs_to_user`. |
| E-23 (setup-demo) | Confirmed/locked-in: `POST /campaigns/setup-demo` already shares the campaign rate-limit bucket; dedicated test added. |
| New: RBAC | `User.role` is now meaningful. `require_roles(*roles)` dependency factory (401 unauthenticated / 403 wrong role, structured `authx.role_denied` event) backs a new admin-only surface: `GET /api/v1/auth/users` (directory) and `PATCH /api/v1/auth/users/{id}/role` (role assignment; self-demotion returns 400 so the last admin cannot lock everyone out). Registration promotes emails listed in `settings.BOOTSTRAP_ADMIN_EMAILS` (whitespace/case-insensitive) to `admin`; everyone else is `researcher`. |

New tests: `tests/test_authz_admin.py` (10) — bootstrap promotion (case/whitespace), default researcher, admin directory, 401 / 403 guards, promote-success reflected via `/me`, researcher 403 on role change, self-demotion 400, invalid role 422, unknown user 404. `tests/test_rate_limiting.py` (+4): setup-demo on the campaign bucket, XFF spoofing ignored without a trusted proxy, XFF honored for a trusted proxy, and CIDR allowlist matching.

---

## 18. Phase 11 — Finding evidence + frontend campaign views (2026-09-06)

| Issue | Resolution |
|---|---|
| E-21 (frontend dead ends) | The campaign detail page now renders the real data instead of the generated report only: new `BudgetPanel`, `FindingsView`, `MutationTree`, `SeverityBadge` components wired into `frontend/src/app/campaigns/[id]/page.tsx`. |
| E-21 (backend gaps) | Migration `b1a2c3d4e5f6` adds `vulnerabilities.evaluator_evidence` / `verifier_evidence` (quoted verdict evidence retained for the dashboard); the campaigns router exposes per-campaign findings/budget/mutation-tree data; the vulnerabilities router + evaluation/verification services persist evidence. |

Commit `e22ca65 "phase 11 hogya"`. Test count at the next phase's baseline: the suite also gained evidence-column migration round-trip coverage.

---

## 19. Phase 12 — Analytics + reproducible benchmark framework (2026-09-06)

| Issue | Resolution |
|---|---|
| E-22 (no benchmark framework) | New `benchmarks/` package (kept outside `app/` — it is a runtime import, not part of the pip module): `metrics.py` holds the single pure metric-math module (zero DB/network), `scenarios.py` defines guardrail probe scenarios, `runner.py` drives mock-provider campaign ablations, and `run_benchmark.py` is the CLI (`python -m benchmarks.run_benchmark --json` writes `benchmark-report.json`). |
| E-22 (no metrics endpoint) | New `MetricsService` (`app/services/metrics.py`) aggregates persisted telemetry (attacks, verified findings by severity, token ledger) into the same `AggregatedMetrics` bundle the benchmark uses, so the API and the harness agree. `GET /api/v1/analytics/metrics` (user-scoped) and `GET /api/v1/analytics/experiments/{id}/metrics` (ownership-checked) sit behind `app/api/routers/analytics.py`. |
| Tooling | `make benchmark` + CI benchmark gate; `.gitignore` covers generated reports. |

Commit `c2fe7b4 "uhhhh"`. **152/152 tests pass** (baseline verified at the start of Phase 13). Lint + typecheck + benchmark gates green.

---

## 20. Phase 13 — Observability + onboarding completion (2026-09-06)

**161/161 tests pass.** Lint + typecheck + benchmark gates green.

| Issue | Resolution |
|---|---|
| E-20 (structured LLM-call records) | `LiteLLMTargetProvider` emits a structured `provider.llm_call` event (model, latency, prompt/completion/total tokens) on every successful completion — the four agents' calls are all covered. |
| E-20 (campaign_id on provider errors) | `BudgetedTargetProvider` — the only layer that always knows the campaign + role — emits a campaign-scoped `provider.llm_error` event (campaign_id, role, model, error_message) when a wrapped delegate returns an error. `ExecutionService` logs (`execution.attack_executed` / `execution.provider_error`) now carry `campaign_id` (and `strategy`). |
| E-20 (masking) | `MaskingFormatter` moved from 3 blunt substring replacements to a single-pass regex redactor covering `api_key`/`x-api-key`/`client_secret`/`access_token`/`refresh_token`/`authorization`/`password`/`private_key`/`secret`, `Bearer <token>`, raw `sk-...`, and JWT `eyJ...` headers — no value can re-trigger a sibling rule. The JSON context-field whitelist gained `role`, token counters, `error_message`, `transient`, and `path`. |
| E-17 (observability hardening) | `alembic/env.py` now calls `fileConfig(..., disable_existing_loggers=False)`: in-process migrations were silently disabling every pre-existing application logger for the rest of the process (surfaced by the new logging tests). |
| E-17 (onboarding remainder) | Root `README.md` (architecture, local + Docker quickstart, configuration table, Makefile reference, API surface, CI/testing, governance). `backend/Dockerfile` (one image, API default cmd + worker override, unprivileged `oracle` user) and `frontend/Dockerfile` (multi-stage Next.js 16 production build with build-arg `NEXT_PUBLIC_API_URL`) plus matching `.dockerignore` files. `docker-compose.yml` now runs the full stack: pgvector `db` + `redis` healthchecks, `backend` (:8000), `worker` (recovery-aware), `frontend` (:3000); `SECRET_KEY` is mandatory (fails loudly), LLM keys and admin emails pass through. Compose config validated via `docker compose config`. |

New tests: `tests/test_observability.py` (9) — key-value/Bearer/JWT/`sk-` masking, single-pass redaction (no `=***=***` cascade), whitelist enforcement (unknown extras dropped), `provider.llm_call` event shape, wrapper `provider.llm_error` campaign context, and no double-logging on success.

Still open (deliberate, non-blocking): Docker image builds could not be smoke-tested on this machine (Docker daemon unavailable; only `docker compose config` was verifiable).