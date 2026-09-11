# Jailbreak Oracle — Implementation Tracker

## COMPLETED

### Phase 1 — Foundation
- [x] Fixed broken import in `app/main.py` (`db.database` → `db.session` + `db.base`)
- [x] Established ONE canonical FastAPI app (`app/main.py`) with lifespan, CORS, health endpoint
- [x] Established ONE canonical database/session (`app/db/session.py`)
- [x] Established ONE canonical User/domain model (`app/models/domain.py`)
- [x] Established ONE canonical LLM provider abstraction (`app/targets/`)
- [x] Configuration from environment/settings via `app/core/config.py` (pydantic-settings)
- [x] Added missing dependencies to `requirements.txt` (litellm, python-jose, passlib, bcrypt, aiosqlite)
- [x] Removed hardcoded mock providers from production code paths
- [x] Fixed `deps.py` — removed test imports, uses configurable providers
- [x] Fixed `campaigns.py` router — uses settings-based providers
- [x] Fixed `vulnerabilities.py` router — uses settings-based providers
- [x] Removed duplicate top-level `backend/main.py` health endpoint (kept as minimal fallback)
- [x] All 21 tests pass

### Phase 2 — Real LLM Campaign Pipeline
- [x] Configurable attacker provider/model (`ATTACKER_PROVIDER`, `ATTACKER_MODEL`)
- [x] Configurable evaluator provider/model (`EVALUATOR_PROVIDER`, `EVALUATOR_MODEL`)
- [x] Configurable verifier provider/model (`VERIFIER_PROVIDER`, `VERIFIER_MODEL`)
- [x] Configurable target provider/model (`DEFAULT_TARGET_PROVIDER`, `DEFAULT_TARGET_MODEL`)
- [x] Configurable embedding provider (`EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`)
- [x] Campaign pipeline reads target type from DB — uses `TargetFactory.get_provider(target.provider_type)`
- [x] `initialize_experiment` + `run_attack_loop` background execution
- [x] Auth router wired into main app (`core/auth.py`, `api/routers/auth.py`)

### Phase 3 — Attack Engine
- [x] `AttackStrategy` ABC with `name`, `category`, `get_generation_prompt`, `metadata`
- [x] Strategy registry (`strategies/registry.py`) with auto-registration
- [x] Direct prompt injection strategy
- [x] Role-play/persona manipulation strategy
- [x] Instruction hierarchy attacks strategy
- [x] Encoding/obfuscation strategy
- [x] Context manipulation strategy
- [x] Multi-turn attack strategy
- [x] Adaptive mutation strategy

### Phase 4 — Adaptive Attack Loop
- [x] OBSERVE → PLAN → ATTACK → EVALUATE → LEARN → MUTATE → RETRY → VERIFY → RECORD pipeline
- [x] Strategy selection based on exploration/exploitation ratio
- [x] Consecutive failure tracking with mutation hints
- [x] Campaign state persisted (PENDING → RUNNING → COMPLETED/FAILED with timestamps)

### Phase 5 — RAG / Learning
- [x] `MemoryService` with pgvector similarity search (PostgreSQL)
- [x] SQLite fallback via ORM-based basic search
- [x] Embedding generation (OpenAI or Mock)
- [x] Attacker agent uses RAG context from past attacks

### Phase 6 — Verifier + Defense
- [x] `VerifierAgent` with independent LLM verification
- [x] `VerificationService` — dual verification separate from evaluator
- [x] Verifier integrated into campaign pipeline
- [x] Vulnerability status tracking (UNCONFIRMED → CONFIRMED_VULNERABILITY / FALSE_POSITIVE)
- [x] `HeuristicEngine` for deterministic refusal detection

### Phase 7 — Frontend
- [x] Dashboard with stats cards (active campaigns, vulnerabilities, reports)
- [x] Campaign list page with status badges
- [x] New campaign form with demo setup
- [x] Reports list and detail pages with risk scores
- [x] API client configured for backend

## REMAINING / FUTURE

### Phase 7 — Frontend Enhancements
- [ ] Attack strategy selection UI (currently hardcoded in campaign creation)
- [ ] Iteration configuration UI
- [ ] Campaign monitoring/progress display
- [ ] Attack attempts and target responses display
- [ ] Evaluation/verifier results display
- [ ] Attack history and analytics

### Phase 8 — Production Hardening
- [ ] Authentication middleware on protected routes
- [ ] Rate limiting middleware
- [x] Redis job queue for background workers (superseded: durable PostgreSQL
      `campaign_jobs` queue — Redis removed entirely)
- [ ] Structured logging
- [ ] Comprehensive error handling
- [ ] API input validation refinements
- [ ] Frontend production build verification
- [ ] Deployment configuration (Docker, env vars)

## TESTS
- 21/21 passing
- Backend import/startup: verified
- API health check: verified
- Campaign API flow: verified
- Full campaign orchestration loop: verified
- Database CRUD: verified
- Attack generation: verified
- Evaluation pipeline: verified
- Execution service: verified
- Memory/RAG: verified (SQLite fallback)
- Report generation: verified
- Verification workflow: verified
- Target factory: verified
- LiteLLM provider (mocked): verified
- Embedding provider (mocked): verified
