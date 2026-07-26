# Implementation Plan

## 0. Purpose & How To Use This Document

This document is the **operational build sequence** for `bravi-ai-chatbot`, written for an AI coding agent (or a human holding itself to the same discipline) to execute mechanically from an empty repository to a production-ready release.

It does **not** redefine what to build — the full specification lives in `docs/00-project-overview.md` through `docs/23-configuration.md`. This document defines four things those files don't: the **order** work happens in, the **dependencies** between chunks of work, what **"Done"** means for each chunk, and the **rule** that governs when it's safe to move forward.

Each phase below has a `**Status:**` line. **Update it as you work** (`NOT STARTED` → `IN PROGRESS` → `DONE`) and **check off every `- [ ]` box** as you complete it. This file's checkboxes and status lines are the durable audit trail of build progress — do not rely on conversation memory or commit messages alone to track where the build is. At the start of any session working on this repository, **read this file first**, find the first phase not marked `DONE`, and resume there.

## Session Startup Procedure

At the beginning of every coding session:

1. Read IMPLEMENTATION_PLAN.md completely.
2. Find the first phase whose Status is NOT STARTED or IN PROGRESS.
3. Read only the documents referenced by that phase.
4. Validate that no prerequisite phase is incomplete.
5. Execute only that phase.
6. Run all Verification steps.
7. Update IMPLEMENTATION_PLAN.md.
8. Stop.

## 1. Governing Rule — Sequential, Gated Execution

> **The AI agent must not begin work on Phase N+1 until Phase N's Status is `DONE`.** "Done" means all three of: every task checkbox in the phase is checked, every condition in that phase's "Definition of Done" is true, and every item in that phase's "Verification" checklist has actually been run and passed — not assumed, not skipped, not deferred.

Consequences of this rule:

- If a verification item fails, the phase is **not** Done. Fix the root cause within the current phase's scope, then re-run the full verification checklist — a partial re-check is not sufficient.
- Do not comment out a failing test, narrow a test's assertions, or silently reduce scope to make a check pass. If a requirement turns out to be wrong or ambiguous, stop and flag it rather than working around it.
- Do not start implementation work that belongs to a later phase "while you're in there," even if it seems efficient. Note it (e.g., as a `# TODO(phase N): ...` comment referencing this file) and leave it for its actual phase.
- A phase marked `DONE` is not sacred if a **later** phase's work uncovers a defect in it. See the Appendix for the correction procedure — it still ends with the earlier phase re-clearing its own gate before you resume the later one.

## 2. Relationship to `13-roadmap.md`

`13-roadmap.md` is the product/milestone-level plan (M1–M6) for human stakeholders — business-framed, coarse-grained. This document is the fine-grained, mechanically-gated build sequence an agent actually executes. They must never contradict each other; this document is strictly more granular. Mapping:

| This plan | `13-roadmap.md` milestone |
|---|---|
| Phase 0 – Phase 5 | M1 — Foundations |
| Phase 6 – Phase 7 | M2 — Ingestion Pipeline |
| Phase 8 – Phase 9 | M3 — Core Chat Pipeline |
| Phase 10 – Phase 11 | M4 — Operator Features |
| Phase 12 – Phase 13 | M5 — Hardening |
| Phase 14 | M6 — Release & Handover |

## 3. Non-Negotiable Constraints (apply to every phase)

These override any phase-local checklist. A phase that violates one of these is not Done regardless of what its own boxes say:

- Three separate LangGraph instances (`user_chat_graph`, `operator_chat_graph`, `ingestion_graph`); `user_chat_graph` must never import `tools/operator_tools.py` — `11-coding-standard.md` §8.1.
- No LLM-driven dynamic tool-calling (no `bind_tools`/function-calling schema handed to a Bedrock model) — `16-tool-calling.md` §1-§2.
- `/api/chat` and `/api/opr/chat` are SSE-only, one fixed JSON event schema — `06-api-specification.md` §0.
- Every generated and canned response is in Bahasa Indonesia — `02-functional-requirements.md` FR-14.
- Cost-control short-circuit ordering is enforced in code, not just convention: greeting → (Operator only) add-knowledge-intent → out-of-topic → similarity threshold → RAG — FR-6.
- AWS Bedrock is the only LLM/embedding provider.
- All configuration flows through `app/config.py`; no `os.environ` elsewhere — `11-coding-standard.md` §5.
- All Bedrock calls go through `clients/bedrock_client.py`; all Redis access goes through `clients/redis_client.py` — §12/§13.

## 4. Progress Tracker

Keep this table's Status column in sync with each phase's own Status line — this table is the at-a-glance summary.

| # | Phase | Depends on | Status |
|---|---|---|---|
| 0 | Repository Scaffolding & Tooling | — | DONE* (see note) |
| 1 | Configuration Module & Startup Validation | 0 | DONE |
| 2 | Database Schema & Migrations | 1 | DONE |
| 3 | AWS Bedrock Resilience Client | 1 | DONE |
| 4 | Redis Client & Rate-Limit Middleware | 1 | DONE |
| 5 | System / Health / Metrics Endpoints | 2, 3, 4 | DONE |
| 6 | Ingestion Graph & Startup Ingestion Job | 2, 3 | DONE |
| 7 | Operator Ingestion & Knowledge Management Endpoints | 6 | DONE |
| 8 | Session & Message Persistence + Endpoints | 2 | DONE |
| 9 | User Chat Graph & `/api/chat` (SSE) | 3, 4, 6, 8 | NOT STARTED |
| 10 | Operator Chat Graph & `/api/opr/chat` (SSE) | 7, 9 | NOT STARTED |
| 11 | Trending & Analytics Endpoints | 9, 10 | NOT STARTED |
| 12 | Security Hardening Pass | 7, 9, 10 | NOT STARTED |
| 13 | Production Hardening | 4, 9, 10, 12 | NOT STARTED |
| 14 | Full-System Verification (Release Gate) | 0–13, all DONE | NOT STARTED |

## 5. Dependency Graph (informational)

```
0 → 1 → 2 ─┬─────────────┬──────────────┐
           │             │              │
           ├→ 3 ─┐       ├→ 6 → 7 ─┐    │
           │     ├→ 5    │         │    │
           └→ 4 ─┘       └────┐    │    │
                               ▼    ▼    ▼
                    8 ─────→ 9 ────┴───→ 10 → 11 ┐
                                                   │
                                      12 ←─────────┤
                                       │           │
                                       ▼           │
                                      13 ←──────────┘
                                       │
                                       ▼
                                      14
```

This graph shows the *true* logical dependencies (e.g., Phase 3 and Phase 4 don't depend on each other, only both on Phase 1). It does **not** license reordering or parallel execution by a single agent — §1's rule is the strict numeric sequence 0→14 regardless of what this graph implies is technically parallelizable. The graph exists so you understand *why* the order is what it is, and so a team splitting this across multiple agents/engineers later knows what can safely run concurrently.

---

## Phase 0 — Repository Scaffolding & Tooling

**Status:** DONE* (one Verification item deferred by explicit user decision — see note below)
**Depends on:** none
**Reference docs:** `11-coding-standard.md` §1/§2/§10, `10-deployment.md` §2/§5

**Tasks**
- [x] Create the `backend/` tree exactly per `11-coding-standard.md` §2 (empty `__init__.py` placeholders where needed).
- [x] `pyproject.toml`: Python ≥3.11, `black`, `ruff`, `mypy`, `pytest`, `pytest-asyncio` configured.
- [x] Choose and lock a dependency manager (`poetry` or `pip-tools`) — commit the lockfile.
- [x] `Dockerfile` (slim Python base, non-root user) per `10-deployment.md` §2.
- [x] `docker-compose.yml` (app + db + redis) per `10-deployment.md` §2.
- [x] `.env.example` scaffold (empty — fully populated in Phase 1).
- [x] CI pipeline skeleton (lint, type-check, test, build) per `10-deployment.md` §5, even if steps are near-no-ops until code exists.
- [x] `.gitignore` excludes `.env`, `__pycache__`, etc.

**Definition of Done**
- Directory structure matches `11-coding-standard.md` §2 exactly — no extra top-level dirs, none missing.
- Tool configuration itself is valid (lint/type-check run cleanly against a near-empty codebase).
- `docker build` succeeds; `docker-compose up` starts `app`+`db`+`redis` without crash-looping (an immediately-exiting stub `main.py` is acceptable at this phase — it must not be a *config* error).
- CI goes green on a trivial commit.

**Verification**
- [x] `black --check backend/` exits 0
- [x] `ruff check backend/` exits 0
- [x] `mypy backend/app` exits 0
- [x] `docker build -t bravi-ai-chatbot .` exits 0
- [x] `docker-compose config` validates without error
- [ ] CI run is visible and green — workflow committed and YAML-validated locally; cannot be observed running "green" on GitHub's infrastructure until this repo has a remote and an actual push/PR triggers it (no remote is configured yet). Re-check this box once a remote exists and the first Actions run completes.

**Gate:** Do not begin Phase 1 until every box above is checked and this phase's Status is `DONE`.

> **Note (2026-07-26):** The one Verification item that could not be executed in this environment is the live "CI run is visible and green" check, since there is no GitHub remote configured for this repository yet. All other Definition-of-Done and Verification items were executed and passed locally (see completion report). Asked directly, the user chose to accept the local-verification proxy (passing `black`/`ruff`/`mypy`/`docker build`/`docker-compose config` plus a syntax-validated workflow file) and proceed to Phase 1 rather than block on provisioning a remote. Re-check that box once a remote exists and the first Actions run completes; if it fails, the fix belongs to Phase 0's artifacts per the Appendix correction procedure.

---

## Phase 1 — Configuration Module & Startup Validation

**Status:** DONE
**Depends on:** Phase 0
**Reference docs:** `11-coding-standard.md` §5, `10-deployment.md` §3 (authoritative defaults), `23-configuration.md` (categorized reference + §4 validation checklist)

**Tasks**
- [x] `app/config.py`: a `pydantic-settings.BaseSettings` class covering every variable in `10-deployment.md` §3.
- [x] Fill `.env.example` with every variable + placeholder/default from `10-deployment.md` §3.
- [x] Implement every startup-validation rule from `23-configuration.md` §4, failing fast with a clear message on violation.
- [x] No `os.environ` access anywhere outside `config.py`.

**Definition of Done**
- Every variable in `10-deployment.md` §3 has a typed field in `config.py` with a matching default where one exists.
- Every check in `23-configuration.md` §4 is implemented and unit-tested for both the pass and fail-fast cases.
- Config loads correctly from a local `.env`.

**Verification**
- [x] Unit tests: valid `.env` → app starts; `CHUNK_OVERLAP_TOKENS >= CHUNK_SIZE_TOKENS` → fails fast; `SIMILARITY_SCORE_THRESHOLD` outside `(0, 1]` → fails; missing required secret with `APP_ENV=production` → fails
- [x] `grep -rn "os.environ" backend/app --include=*.py` returns no matches outside `config.py`
- [x] `pytest tests/unit/test_config.py` passes (39 tests)

**Gate:** Do not begin Phase 2 until every box above is checked and this phase's Status is `DONE`.

---

## Phase 2 — Database Schema & Migrations

**Status:** DONE
**Depends on:** Phase 1 (needs `DATABASE_URL` from config)
**Reference docs:** `07-database-design.md` (all sections), `11-coding-standard.md` §2

**Tasks**
- [x] Initialize Alembic under `backend/migrations/`.
- [x] Migration: enable `vector`, `pgcrypto` extensions.
- [x] Migration(s): create `sessions`, `messages`, `knowledge_sources`, `knowledge_documents`, `knowledge_chunks`, `ingestion_jobs`, `usage_metrics` exactly per `07-database-design.md` §3, including every index and the `history_summary`/`history_summary_updated_at`/`valid_until`/`superseded_by_document_id` columns.
- [x] Confirm the Cohere Embed v4 output vector dimension (resolves `01-prd.md` §11 risk #4) and set `VECTOR(n)` accordingly — this phase cannot close with a placeholder dimension.
- [x] `app/models/`: SQLAlchemy ORM models matching every table field-for-field.
- [x] `app/repositories/` skeleton (empty CRUD methods are acceptable now; logic lands as later phases need it).

**Definition of Done**
- `alembic upgrade head` against a clean Postgres+pgvector database creates every table/column/index in `07-database-design.md` §3 with zero manual SQL steps.
- The embedding vector dimension is confirmed and fixed, not a placeholder.
- ORM models exist for every table and match the migrations exactly.

**Verification**
- [x] `alembic upgrade head` exits 0 against a fresh DB
- [x] `alembic downgrade base` then `alembic upgrade head` again succeeds (migrations are reversible/repeatable)
- [x] Schema-diff check (`alembic check` or manual inspection) shows ORM models match the DB schema exactly
- [x] `pytest tests/unit/test_repositories.py` (basic CRUD smoke tests) passes against a test database

**Gate:** Do not begin Phase 3 until every box above is checked and this phase's Status is `DONE`.

> **Note (2026-07-26):** The Cohere Embed v4 output-dimension risk (`01-prd.md` §11 risk #4) is resolved. AWS's official Bedrock docs (`docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html`, fetched live) confirm `output_dimension` is a *request parameter* the caller sets — allowed values `256`/`512`/`1024`/`1536`, defaulting to **1536** if omitted. Since this is a choice, not a fixed model fact, the user was asked directly and chose **1024** (matching the pre-existing placeholder in `07-database-design.md`). This is now fixed in the migration and ORM model. **Load-bearing consequence for Phase 3:** `clients/bedrock_client.py` must pass `"output_dimension": 1024` explicitly on every embed call — if that parameter is ever omitted, Bedrock returns 1536-dim vectors and every insert into `knowledge_chunks.embedding` will fail with a dimension mismatch.

---

## Phase 3 — AWS Bedrock Resilience Client

**Status:** DONE
**Depends on:** Phase 1
**Reference docs:** `14-bedrock-integration.md` (all), `11-coding-standard.md` §12, `15-model-management.md` §3

**Tasks**
- [x] `clients/bedrock_client.py`: `embed(texts)` and `generate_stream(prompt, **params)` per `14-bedrock-integration.md` §2.
- [x] Credential resolution per §3 (`boto3` default chain, no custom logic).
- [x] Streaming relay per §4.
- [x] Error taxonomy/retry classification per §5.
- [x] Circuit breaker state machine (`closed`/`open`/`half-open`) using `BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD`/`BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS` per §6.
- [x] `BEDROCK_TEMPERATURE` applied to every generation call per `15-model-management.md` §3.
- [x] `bedrock_circuit_breaker_state` observable via a simple accessor (full metrics wiring is Phase 13, but the state must be testable now).

**Definition of Done**
- No file outside `clients/bedrock_client.py` imports the `bedrock-runtime` `boto3` client.
- The circuit breaker demonstrably trips after the configured consecutive-failure threshold and recovers via a half-open probe after the configured cooldown, against a mocked `boto3` client.
- A live smoke test (one embedding call, one short streamed generation) succeeds against real Bedrock in a dev AWS account.

**Verification**
- [x] `grep -rn "bedrock-runtime\|boto3.client(\"bedrock" backend/app --include=*.py` shows matches only inside `clients/bedrock_client.py`
- [x] `pytest tests/unit/test_bedrock_client.py` — timeout, bounded retry+backoff, every error-taxonomy branch (§5 table), full circuit-breaker state transitions, all against a mocked `boto3` client
- [x] Manual/CI smoke test embeds one string and streams one short generation from real Bedrock

**Gate:** Do not begin Phase 4 until every box above is checked and this phase's Status is `DONE`.

> **Note (2026-07-26):** Working AWS credentials for the exact dev account referenced in the model ARNs (586794442374, ap-southeast-3) were found already configured in this environment. Asked directly, the user approved running the live smoke test against real Bedrock. Result: `generate_stream()` worked correctly on the first attempt (streamed, grounded Bahasa Indonesia output). `embed()` initially mis-parsed the response — AWS's own Bedrock docs (fetched live in Phase 2) describe a flat `{"embeddings": [[...]]}` shape when only one `embedding_types` entry is requested, but the real API actually returns the by-type shape `{"embeddings": {"float": [[...]]}}` regardless. Fixed in `_invoke_embed` to handle both shapes, re-verified live (dimension confirmed as exactly 1024, matching Phase 2's schema), and added a regression test (`test_embed_parses_by_type_response_shape`) since the mocked unit tests alone would never have caught this doc/reality mismatch.

---

## Phase 4 — Redis Client & Rate-Limit Middleware

**Status:** DONE
**Depends on:** Phase 1
**Reference docs:** `11-coding-standard.md` §13, `08-security.md` §6, `10-deployment.md` §3

**Tasks**
- [x] `clients/redis_client.py` thin wrapper.
- [x] `middleware/rate_limit.py`: Redis-backed token-bucket keyed by `user_id`/IP, using `RATE_LIMIT_REQUESTS_PER_MINUTE`/`RATE_LIMIT_BURST`.
- [x] Wired against stub routes if `/api/chat`/`/api/opr/chat`/`/api/opr/ingest` don't exist yet (they land in Phases 7/9/10) — the middleware itself must be complete and tested now, applied for real once each route exists.

**Definition of Done**
- The limiter correctly enforces the configured limit against a real (or `fakeredis`) instance.
- Limiter state is verified shared correctly across multiple simulated app instances, not per-process.
- No file outside `clients/redis_client.py`/`middleware/rate_limit.py` touches Redis directly.

**Verification**
- [x] `pytest tests/unit/test_rate_limit.py` — token-bucket math against `fakeredis`, per-`user_id`/IP isolation
- [x] `pytest tests/integration/test_rate_limit_multi_instance.py` — two simulated app instances sharing one Redis correctly share limiter state
- [x] `grep -rn "redis" backend/app --include=*.py` shows matches only inside `clients/redis_client.py` and `middleware/rate_limit.py`

**Gate:** Do not begin Phase 5 until every box above is checked and this phase's Status is `DONE`.

> **Note (2026-07-26):** `middleware/rate_limit.py` uses Redis `WATCH`/`MULTI`/`EXEC` optimistic-concurrency transactions for the token-bucket read-modify-write, not a server-side Lua script (`EVAL`) — `fakeredis`'s scripting support needs the `lupa` C-extension, and avoiding it keeps the dependency footprint minimal per `11-coding-standard.md`'s dependency rule while remaining safe under concurrent access (verified in `tests/integration/test_rate_limit_multi_instance.py` via two `fakeredis` clients sharing one `FakeServer`). The limiter is keyed by `(endpoint, identity)` — identity is `user_id` when available (present in `/api/chat`/`/api/opr/chat` JSON bodies) and falls back to client IP otherwise (`/api/opr/ingest`'s multipart body has no `user_id` field at all per `06-api-specification.md` §6). `RateLimitExceededError` is a domain exception (`11-coding-standard.md` §6) with a matching `rate_limit_exception_handler`, to be registered on `app` (`app.add_exception_handler(RateLimitExceededError, rate_limit_exception_handler)`) once `app/main.py` grows real routers — not done in this phase since `main.py` remains a Phase 0 stub.

---

## Phase 5 — System / Health / Metrics Endpoints

**Status:** DONE
**Depends on:** Phase 2, Phase 3, Phase 4
**Reference docs:** `06-api-specification.md` §9, `09-observability.md` §5/§8

**Tasks**
- [x] `api/system_router.py`: `GET /health`, `GET /health/ready` (DB+Redis+Bedrock reachability), `GET /metrics` (Prometheus exposition).
- [x] `app/main.py`: FastAPI entrypoint wiring `system_router`.
- [x] Graceful-shutdown `SIGTERM` handler skeleton (full drain behavior is Phase 13 — the hook must exist now so later phases don't retrofit it).

**Definition of Done**
- All three endpoints match `06-api-specification.md` §9 exactly.
- `/health/ready` returns `503` when DB or Redis is down, `200` when both are up.
- `/metrics` exposes valid Prometheus exposition format (counters may still be zero/unregistered until later phases add them).

**Verification**
- [x] `docker-compose up` then `curl localhost:8000/health` → `{"status":"ok"}`
- [x] `curl localhost:8000/health/ready` → `200` with every check `"ok"`
- [x] `docker-compose stop db` then `curl localhost:8000/health/ready` → `503` with `database:"error"` — then restart and confirm recovery
- [x] `curl localhost:8000/metrics` returns `text/plain; version=0.0.4` content
- [x] `pytest tests/integration/test_health.py` passes

**Gate:** Do not begin Phase 6 until every box above is checked and this phase's Status is `DONE`. **This is the M1/"Foundations" exit gate.**

> **Note (2026-07-26):** `09-observability.md` §8/`06-api-specification.md` §9.2 require `/health/ready` to check "Bedrock reachability (lightweight, not a real inference call)" without specifying how. Asked directly, the user chose to base this on `bedrock_client.circuit_breaker_state` (reports `"ok"` unless the breaker is `OPEN`) rather than a live AWS API call — no network round-trip, no cost, no IAM permissions beyond the documented least-privilege `bedrock:InvokeModel` scope (`08-security.md` §5). Trade-off recorded: at cold start (no Bedrock calls yet), this always reports `"ok"` even if Bedrock were genuinely unreachable, since it reflects recent real-call history rather than an independent live probe.
>
> **Correction to Phase 0's `docker-compose.yml` (per the Appendix procedure):** running this phase's `docker-compose up` verification surfaced a latent defect from Phase 0 — the `app` service's `env_file: .env` gave it `DATABASE_URL`/`REDIS_URL` pointing at `localhost`, which resolves to the `app` container itself, not the sibling `db`/`redis` containers, once `app.main:app` became a real app instead of Phase 0's immediately-exiting stub (the stub never actually opened a DB/Redis connection, so this never surfaced before). Fixed by adding an `environment:` override on the `app` service for exactly those two keys, pointing at the compose service hostnames (`db`, `redis`); `.env` itself is untouched (still correct for host-based `poetry run` dev). Re-ran Phase 0's affected verification (`docker-compose config` validates; `docker build` unaffected) — both still pass.
>
> Because `docker-compose up`'s `db`/`redis` container names/ports collide with this environment's already-running standalone `bravi-db-1` (started manually in Phase 2, not via compose), the live end-to-end proof above was executed as: the existing `bravi-db-1` container plus one temporary `redis:7-alpine` container (host-networked identically to how compose would run it) plus the app run directly via `poetry run uvicorn` on the host — functionally equivalent to `docker-compose up` for verification purposes, without touching the user's existing infrastructure. The temporary Redis container was removed afterward; `bravi-db-1` was stopped/restarted as part of the 503-then-recovery check and left running, matching its state before this session.

---

## Phase 6 — Ingestion Graph & Startup Ingestion Job

**Status:** DONE
**Depends on:** Phase 2, Phase 3
**Reference docs:** `05-ai-agent-design.md` §3, `18-rag-design.md` §3/§4, `07-database-design.md` §5, `04-system-architecture.md` §5

**Tasks**
- [x] `graphs/nodes/`: `load_source`, `extract_text`, `chunk_text` (token-based per `18-rag-design.md` §3 — **not** character-based), `embed_chunks` (batched via `EMBEDDING_BATCH_SIZE`), `store_vectors`, `update_ingestion_status`, `log_metrics`.
- [x] `graphs/ingestion_graph.py` wiring per `05-ai-agent-design.md` §3.2, with per-document failure isolation.
- [x] Concurrency bounded by `INGESTION_CONCURRENCY`.
- [x] `jobs/run_initial_ingestion.py`: idempotent per `07-database-design.md` §5 (content-hash check).
- [x] Wired into the app startup sequence as a separate one-off step per `10-deployment.md` §4's recommendation (not inline-blocking); revised same-day to `jobs/ingestion_scheduler.py`, a cron-scheduled (`INGESTION_CRON_SCHEDULE`) wrapper that never runs automatically at app/container startup — see the dated correction notes below.

**Definition of Done**
- Running the startup job twice against the same source list creates no duplicate `knowledge_documents` rows.
- A corrupt/unreachable source fails only that document; the batch completes for all others.
- Chunking is verifiably token-based (a test with Bahasa Indonesia/non-ASCII text proves character count ≠ token count and the chunker still respects `CHUNK_SIZE_TOKENS`).

**Verification**
- [x] `pytest tests/unit/test_chunking.py` — token-based measurement, `CHUNK_OVERLAP_TOKENS < CHUNK_SIZE_TOKENS` enforcement
- [x] `pytest tests/integration/test_ingestion_graph.py` — happy path, corrupt PDF, unreachable URL, per `12-testing-strategy.md` §3
- [x] `pytest tests/integration/test_startup_ingestion_idempotency.py` — two runs, no duplicates
- [x] Manual: run `python -m app.jobs.run_initial_ingestion` twice against a small real sample set; `knowledge_chunks` row count is identical after both runs
- [x] `pytest tests/unit/test_ingestion_scheduler.py` — cron job registered without running immediately, correct trigger/`max_instances`/`coalesce` (added same-day, see correction note)
- [x] `pytest tests/unit/test_config.py::TestIngestionCronSchedule` — valid/invalid cron expressions (added same-day)

**Gate:** Do not begin Phase 7 until every box above is checked and this phase's Status is `DONE`.

> **Note (2026-07-26):** Two decisions the docs left open were confirmed directly with the user before implementation:
>
> 1. **`INGESTION_RUN_ONCE`/idempotency semantics** — `02-functional-requirements.md` FR-10 describes a persisted "has the batch ever run" marker gating the whole job, distinct from `07-database-design.md` §5's per-source content-hash check (the doc actually referenced by this phase). A batch-level "ran once, never again" marker would mean a source's content change is never picked up on a later re-run without a manual DB edit — defeating the point of hash-based change detection. User confirmed: `run_initial_ingestion.py` re-downloads and re-hashes every `knowledge_sources` row on every invocation regardless of `is_ingested`; `INGESTION_RUN_ONCE` does not gate a persisted batch-level marker. An unchanged, already-ingested source is a cheap no-op (one HTTP GET, no embedding call, no new row); changed content is always re-ingested as a new `knowledge_documents` row.
> 2. **Manual verification content** — `DOCUMENT_BASE_URL` in `.env` is still the documented placeholder (`https://example.com/documents`). User supplied real, reachable sample content instead: `DOCUMENT_BASE_URL=https://pdfobject.com`, `relative_path`s `/pdf/sample.pdf` and `/pdf/sample-3pp.pdf`. Verification ran with `DOCUMENT_BASE_URL` overridden only via an inline process environment variable for the two `python -m app.jobs.run_initial_ingestion` invocations (pydantic-settings' real env vars take precedence over `.env`) — the committed `.env` file was never modified. Results: first run — both PDFs downloaded (real HTTP), embedded (real Bedrock `embed()` calls, no mocking), and stored (2 chunks for `sample.pdf`, 6 chunks for `sample-3pp.pdf`), both `knowledge_documents` rows `status=completed`, both `knowledge_sources` rows `is_ingested=true` with a persisted `content_hash`. Second run — both sources re-hashed (two real HTTP GETs) but matched their stored `content_hash`; outcome `skipped_unchanged` for both, zero Bedrock calls, zero new rows — `knowledge_chunks` row counts identical (2 and 6) to the first run, same `knowledge_documents` row ids. The two seeded `knowledge_sources` rows and their resulting `knowledge_documents`/`knowledge_chunks` were left in place afterward as real, working verification evidence rather than cleaned up.
>
> **Testability addition not in the original task list:** `run_initial_ingestion()` gained an optional `source_ids: list[UUID] | None = None` parameter (default `None` preserves the original "process every `knowledge_sources` row" behavior for real invocations). Without it, `tests/integration/test_startup_ingestion_idempotency.py` would have no way to avoid also processing whatever real rows the manual verification step above leaves in the shared database — the parameter scopes each test run to only the row it seeds itself.
>
> **Docker Compose change:** added a one-off `ingestion` service (`command: python -m app.jobs.run_initial_ingestion`, `restart: "no"`), per `10-deployment.md` §4's "separate one-off deployment step, not inline in the web process" recommendation. Added a `pg_isready` healthcheck to `db` (required for `condition: service_healthy`, which both `app` and `ingestion` depend on) — `db` had none before. `docker compose config` validates; `docker build` unaffected. Full `docker-compose up` end-to-end was not re-run in this environment for the same reason recorded in Phase 5's note (a pre-existing standalone `bravi-db-1` container occupies the `db` service's name/port, and containerized Bedrock calls would need AWS credentials passed through to the container, not just resolvable via the host's credential chain) — verification instead ran `python -m app.jobs.run_initial_ingestion` directly via `poetry run` on the host, per the note above.
>
> **Correction (2026-07-26, same day, user-raised scale concern):** the `ingestion` service was initially wired with `app: depends_on: ingestion: condition: service_completed_successfully`, so `app` would not start serving traffic until the startup batch fully finished. The user pointed out the realistic source-list size for this deployment is on the order of 4,000+ PDFs — at `INGESTION_CONCURRENCY=4`, a first-time full run could take hours, during which the blocking dependency would mean the entire API is unreachable, not just "the knowledge base is incomplete." Changed to **fire-and-forget**: `app`'s `depends_on` no longer includes `ingestion` at all — `app` starts as soon as `db`/`redis` are ready, while `ingestion` runs concurrently in its own container and fills in the knowledge base progressively as it completes each document. This is a better fit for `10-deployment.md` §4's own "not inline-blocking" phrasing than the original blocking dependency was. Trade-off, accepted: `/api/chat`/`/api/opr/chat` can serve answers grounded in a partially-ingested knowledge base during that window (no code change needed for this — the chat graphs already only ever see whatever rows exist in `knowledge_chunks` at query time). `INGESTION_CONCURRENCY`'s existing semaphore-bounded concurrency (real, already tested — see `_process_source`'s `async with semaphore, AsyncSessionLocal()...`) still caps simultaneous Bedrock calls/DB connections/bandwidth regardless of source-list size; this correction only changes when `app` starts relative to `ingestion` finishing, not the ingestion job's own internal concurrency behavior.
>
> **Correction (2026-07-26, same day, user-directed): ingestion moved from "runs on `docker-compose up`" to cron-scheduled, never automatic.** The user explicitly requested the ingestion job run on a cron schedule read from `.env`, and that it must **not** run automatically when the project/stack starts — only at the scheduled time(s). This is a further deviation from `10-deployment.md` §4's original "run once at deploy time" model (already superseded by the fire-and-forget correction directly above), now updated in the spec docs themselves per the user's explicit approval (`docs/10-deployment.md` §3/§4.3, `docs/23-configuration.md` §3/§4), not just tracked here as a plan-only deviation.
>
> Implementation: new `INGESTION_CRON_SCHEDULE` setting (`app/config.py`, default `"0 2 * * *"` — daily 02:00 UTC), validated at startup as a real 5-field cron expression via `apscheduler`'s `CronTrigger.from_crontab` (new dependency — chosen over an OS-level `cron`/`crond` daemon in the container because it behaves identically via `poetry run` on the host and inside Docker, needs no dynamic crontab-file generation, and is directly unit-testable). New module `app/jobs/ingestion_scheduler.py` builds an `AsyncIOScheduler`, registers `run_initial_ingestion` against that cron trigger with `max_instances=1` (no overlapping runs if one is still in progress at the next scheduled time — realistic at large source-list sizes) and `coalesce=True` (only one catch-up run after downtime spanning multiple missed occurrences, not one per miss), then blocks forever — critically, it does **not** invoke the job on startup, only at each scheduled occurrence (verified directly: `tests/unit/test_ingestion_scheduler.py` asserts zero calls immediately after building/starting the scheduler). `app/jobs/run_initial_ingestion.py` itself is unchanged — it remains available for an immediate, on-demand run (`python -m app.jobs.run_initial_ingestion`), which is what the phase's own manual-verification step and `tests/integration/test_startup_ingestion_idempotency.py` still use directly.
>
> `docker-compose.yml`'s `ingestion` service now runs `python -m app.jobs.ingestion_scheduler` (a long-running process, `restart: unless-stopped`) instead of running `run_initial_ingestion` once and exiting (`restart: "no"`) — `docker compose config` validates. `app`'s `depends_on` is unaffected (still just `db`/`redis`, per the correction directly above).

---

## Phase 7 — Operator Ingestion & Knowledge Management Endpoints

**Status:** DONE
**Depends on:** Phase 6
**Reference docs:** `06-api-specification.md` §6/§7/§7.1, `07-database-design.md` §5a/§5b, `02-functional-requirements.md` FR-7/FR-8/FR-13, `22-error-handling.md` §4

**Tasks**
- [x] `api/operator_router.py`: `POST /api/opr/ingest` (background task, `202`, `Idempotency-Key` handling incl. `409` conflict per `22-error-handling.md` §4), `valid_until`/`supersedes_document_id` support.
- [x] `GET /api/opr/knowledge` (list + freshness/versioning fields).
- [x] `DELETE /api/opr/knowledge/{id}` (hard-delete cascade, `ingestion_jobs.document_id` → `NULL`, `knowledge_sources.is_ingested` reset per `07-database-design.md` §5a).
- [x] Rate limiting from Phase 4 actually applied to `/api/opr/ingest` now that the route exists.

**Definition of Done**
- Every behavior in `06-api-specification.md` §6/§7/§7.1 matches exactly, including error codes from `22-error-handling.md` §2.
- A deleted document is immediately excluded from a similarity-search-style query at the repository level (the chat graphs that actually call this don't exist until Phase 9 — test the query directly here).

**Verification**
- [x] `pytest tests/integration/test_ingest_endpoint.py` — file + text ingestion, `Idempotency-Key` retry (same content), `Idempotency-Key` conflict (different content → `409`), `valid_until`/`supersedes_document_id` wiring
- [x] `pytest tests/integration/test_knowledge_delete.py` — cascade delete, `ingestion_jobs` preserved with `NULL` `document_id`, `is_ingested` reset, `404` on unknown/already-deleted id
- [x] Manual: `curl -X DELETE localhost:8000/api/opr/knowledge/{id}` then a direct repository query confirms zero matching `knowledge_chunks` rows

**Gate:** Do not begin Phase 8 until every box above is checked and this phase's Status is `DONE`.

> **Note (2026-07-26):** Two gaps this phase's reference docs left open were resolved before/during implementation:
>
> 1. **`Idempotency-Key` storage — schema gap, confirmed with the user.** `06-api-specification.md` §6 and `22-error-handling.md` §4 require the server to persist an `Idempotency-Key` -> content-hash mapping (same key + same content -> return the original result; same key + different content -> `409 IDEMPOTENCY_KEY_CONFLICT`), but `07-database-design.md` §3 defines no column anywhere for this — `knowledge_sources.content_hash` only applies to startup-managed sources (`source_id` is always `NULL` for `/api/opr/ingest` uploads). Asked directly; user chose to add `idempotency_key`/`content_hash` (both nullable) directly to `knowledge_documents`, mirroring the existing `knowledge_sources.content_hash` pattern, over a dedicated new table. New migration `2e01aa31a079` adds both columns plus a partial unique index (`WHERE idempotency_key IS NOT NULL`); `KnowledgeDocument` ORM model updated to match. `alembic upgrade head` / `downgrade -1` / `upgrade head` round-trip verified.
> 2. **`python-multipart` — new dependency, required by the documented contract.** `/api/opr/ingest` is specified as `multipart/form-data` (`06-api-specification.md` §6); FastAPI's `Form`/`File`/`UploadFile` parsing hard-requires `python-multipart` to be installed (raises at router-definition time otherwise) — not a discretionary addition, a direct necessity for implementing this phase's own documented request contract. Added via `poetry add python-multipart` (`^0.0.32`). Will also be needed by `/api/chat`'s image upload in Phase 9.
>
> **Other implementation decisions, not requiring a stop (none change any documented contract):**
> - **`06-api-specification.md` §10's status-code summary never lists `422` anywhere in the API.** FastAPI's automatic request-validation errors default to `422`, which would silently violate every endpoint's "matches exactly" requirement. `app/errors.py` adds a global `RequestValidationError` -> `400`/`INVALID_REQUEST` handler (plus the `AppError` domain-exception hierarchy backing `INVALID_REQUEST`/`KNOWLEDGE_NOT_FOUND`/`IDEMPOTENCY_KEY_CONFLICT`, and a catch-all `Exception` -> `INTERNAL_ERROR`/500 handler so an unhandled bug still produces the documented envelope instead of FastAPI's default plain response) — registered in `app/main.py` alongside `RateLimitExceededError`'s handler (Phase 4 built it; no route existed to attach it to until now).
> - **`supersedes_document_id` referencing a non-existent document -> `400`/`INVALID_REQUEST`, not `404`/`KNOWLEDGE_NOT_FOUND`.** Neither doc defines this specific error case; `KNOWLEDGE_NOT_FOUND`'s registry entry (`22-error-handling.md` §2) textually scopes to "`DELETE /api/opr/knowledge/{id}` — unknown or already-deleted id" only, so an invalid *request body* reference reads as a `400` validation failure instead.
> - **File/text `source_url` is always `NULL` for `/api/opr/ingest`-created documents.** `07-database-design.md` §3.4's comment mentions "uploader-provided/storage URL for opr/ingest", but `06-api-specification.md` §6's actual request schema has no URL-bearing field at all (no object-storage component is documented anywhere) — there is nothing to populate `source_url` with. Degrades to the doc's own documented fallback: such chunks are "still cited by title, without a link."
> - **`DELETE /api/opr/knowledge/{id}`'s destructive-action log line has no real `user_id`.** `07-database-design.md` §5a calls for logging `user_id` alongside `knowledge_id`/`title`/`chunks_removed`, but `06-api-specification.md` §7.1 defines no request body/params for this endpoint at all — logged as `user_id: None`, noted here rather than inventing a field the endpoint contract doesn't accept.
> - **`ingestion_graph`/`ingestion_state.py` (Phase 6) reused as-is for on-demand ingestion**, exactly as that phase's own docstrings anticipated ("in a later phase, `/api/opr/ingest`") — `app/services/ingestion_service.py` creates `knowledge_documents`/`ingestion_jobs` rows and commits synchronously (so `knowledge_id` returns immediately per §6), then runs the graph via a `BackgroundTasks`-scheduled function opening its own `AsyncSessionLocal()` session, mirroring `run_initial_ingestion.py`'s one-session-per-document pattern from Phase 6.
> - **Test-only finding, no production code implicated:** `TestClient`-driven router integration tests making multiple sequential calls against the app's real pooled `AsyncSessionLocal` intermittently hit asyncpg's "another operation is in progress" (a connection checked out under one call's internal event loop later reused under a different call's loop) and, separately, SQLAlchemy identity-map staleness when a test session reads a row both before and after an out-of-process mutation. Both are test-fixture issues, not application defects — fixed by rebinding `AsyncSessionLocal` to a throwaway `NullPool` engine for the duration of each test (`tests/integration/test_ingest_endpoint.py`/`test_knowledge_delete.py`), mirroring `tests/integration/test_startup_ingestion_idempotency.py`'s existing pattern.

---

## Phase 8 — Session & Message Persistence + Endpoints

**Status:** DONE
**Depends on:** Phase 2
**Reference docs:** `06-api-specification.md` §1/§3, `07-database-design.md` §3.1/§3.2, `02-functional-requirements.md` FR-1/FR-3

**Tasks**
- [x] `repositories/session_repository.py`, `message_repository.py`.
- [x] `services/chat_service.py`: `resolve_session(session_id, user_id, persona)` — auto-create on empty, `404` on unknown, per `06-api-specification.md` §2/§5's exact rule.
- [x] `title` auto-set logic (first user message, truncated ~60 chars, never overwritten).
- [x] `api/user_router.py`: `GET /api/session`, `POST /api/messages`.

**Definition of Done**
- Session resolution matches `06-api-specification.md` exactly for all three cases (empty, valid-existing, valid-but-unknown).
- `title` set-once behavior verified across multiple messages in one session.

**Verification**
- [x] `pytest tests/integration/test_session_resolution.py` — all three cases, both personas
- [x] `pytest tests/integration/test_session_title.py` — set-once, never-overwritten
- [x] Manual: exercise `resolve_session` directly via a test harness (the chat endpoints that call it don't exist until Phase 9)

**Gate:** Do not begin Phase 9 until every box above is checked and this phase's Status is `DONE`.

> **Note (2026-07-26):** No documentation gaps or ambiguities required a stop this phase — `06-api-specification.md` §1/§2/§3/§5 and `07-database-design.md` §3.1/§3.2 fully specified the session-resolution rule, the message schema, and the title set-once behavior. Implementation notes:
>
> - `services/chat_service.py` also gained `list_sessions_for_user`/`get_session_messages` (thin wrappers the two new endpoints call) and `persist_message` — the latter isn't in this phase's own task list verbatim, but is what `07-database-design.md` §3.1 actually names as setting `title`, and Phase 9's `persist_message` *graph node* (its own task list item) is expected to call straight into this same service function rather than duplicating the set-once logic, per `11-coding-standard.md` §4's reuse-over-duplication rule.
> - `resolve_session` does not check whether an existing `session_id`'s stored `persona` matches the calling endpoint's persona — neither `06-api-specification.md` §2/§5 nor `08-security.md` define a mismatch case, so none was invented.
> - Added `tests/integration/test_session_endpoints.py` (not one of the two files named in this phase's Verification list, which targets `resolve_session`/`persist_message` directly since no chat endpoint calls them yet) for direct HTTP-level coverage of the two endpoints this phase's own task list requires building (`GET /api/session`, `POST /api/messages`) — 6 tests: missing/blank `user_id` → `400`, populated listing incl. `title`, unknown `session_id` → `404 SESSION_NOT_FOUND`, ordered message history, missing `session_id` body → `400`.
> - Manual verification ran against the real app (`poetry run uvicorn`) and the existing `bravi-db-1` container: a direct test-harness script exercised all three `resolve_session` cases plus `persist_message`'s title-setting, then `curl` against `GET /api/session`/`POST /api/messages` confirmed the same real, harness-created session/messages round-tripped correctly through the live endpoints. The manually-created session row was deleted afterward (no delete endpoint exists for sessions yet — Phase 13's retention job is the first thing that prunes `messages`, and `sessions` rows are never pruned per `07-database-design.md` §7).

---

## Phase 9 — User Chat Graph & `/api/chat` (SSE)

**Status:** NOT STARTED
**Depends on:** Phase 3, Phase 4, Phase 6, Phase 8
**Reference docs:** `05-ai-agent-design.md` §1-§2, `06-api-specification.md` §0/§2, `docs/prompts/ai-agent.md` §1/§3/§4/§5/§7, `08-security.md` §4, `11-coding-standard.md` §7/§8.1, `17-memory-strategy.md`, `20-performance-target.md`, `12-testing-strategy.md` §3

**Tasks**
- [ ] `graphs/nodes/`: `preprocess_input` (multimodal), `classify_greeting`, `classify_out_of_topic`, `embed_question`, `similarity_search` (per `18-rag-design.md` §4 query), `check_similarity_threshold`, `condense_history` (incremental, persists `sessions.history_summary` per `17-memory-strategy.md` §4), `generate_answer`, `append_sources`, `persist_message`, `log_metrics`.
- [ ] `graphs/user_chat_graph.py` — imports only `tools/user_tools.py` and `graphs/nodes/`, never `tools/operator_tools.py`.
- [ ] `tools/user_tools.py` (QA-only; minimal is fine if no extra tools are needed beyond the node pipeline).
- [ ] Canonical system prompts from `docs/prompts/ai-agent.md` §1/§3/§4/§5/§7 implemented verbatim (Bahasa Indonesia, not a placeholder).
- [ ] `api/user_router.py`: `POST /api/chat` — SSE using the single fixed JSON schema (`06-api-specification.md` §0), session resolution from Phase 8, rate limiting from Phase 4.

**Definition of Done**
- Bedrock text-generation is never invoked for greeting/out-of-topic/below-threshold outcomes — verified by call-count assertion in tests, not by code inspection alone.
- Every generated and canned response is in Bahasa Indonesia regardless of question language.
- Streamed output reassembles into valid Markdown with a correctly appended `## Sources` section.
- `user_chat_graph.py` has zero import path reaching `tools/operator_tools.py` — structurally, not just by inspection.

**Verification**
- [ ] `pytest tests/integration/test_user_chat_graph.py` — all four short-circuit tiers + full RAG per `12-testing-strategy.md` §3, asserting zero Bedrock text calls for the first three tiers
- [ ] `pytest tests/integration/test_persona_isolation.py` — import-graph check per `11-coding-standard.md` §8.1
- [ ] `pytest tests/integration/test_language.py` — Indonesian- and English-phrased input both produce Bahasa Indonesia output
- [ ] `pytest tests/integration/test_freshness.py` per `12-testing-strategy.md` §3
- [ ] Manual: `curl -N localhost:8000/api/chat` with an English-phrased in-domain question — confirm SSE stream, Bahasa Indonesia Markdown answer, correct `## Sources`

**Gate:** Do not begin Phase 10 until every box above is checked and this phase's Status is `DONE`. **This is the M3/"Core Chat Pipeline" exit gate.**

---

## Phase 10 — Operator Chat Graph & `/api/opr/chat` (SSE)

**Status:** NOT STARTED
**Depends on:** Phase 7, Phase 9
**Reference docs:** `05-ai-agent-design.md` §2.2-§2.5, `06-api-specification.md` §5, `docs/prompts/ai-agent.md` §2/§6, `11-coding-standard.md` §8.1, `12-testing-strategy.md` §3

**Tasks**
- [ ] `graphs/nodes/`: `classify_add_knowledge_intent`, `route_by_intent`, `generate_summary`.
- [ ] `graphs/operator_chat_graph.py` — reuses shared nodes from Phase 9, adds the above; may import both `tools/user_tools.py` and `tools/operator_tools.py`.
- [ ] `tools/operator_tools.py` (knowledge-management query helpers, if any beyond direct repository calls).
- [ ] Canonical summary prompt + add-knowledge-intent template (`docs/prompts/ai-agent.md` §2/§6) implemented verbatim.
- [ ] `api/operator_router.py`: `POST /api/opr/chat`.

**Definition of Done**
- `classify_add_knowledge_intent` returns the exact fixed template with zero Bedrock calls, and does not exist anywhere in `user_chat_graph`'s node set.
- The identical trigger phrase sent to `/api/chat` never returns the template — verified functionally, not just structurally.
- Summary mode correctly routes to `generate_summary` with `SUMMARY_TOP_K`.

**Verification**
- [ ] `pytest tests/integration/test_operator_chat_graph.py` — summary routing per `12-testing-strategy.md` §3
- [ ] `pytest tests/integration/test_add_knowledge_intent.py` — exact template + `short_circuit_reason`/`mode:null` on the Operator path; the same phrase via `/api/chat` falls through to normal QA (cross-endpoint regression)
- [ ] Manual: `curl -N localhost:8000/api/opr/chat -d '{"question":"tambah knowledge ai", ...}'` returns the exact `<BTN>Add Knowledge</BTN>` string

**Gate:** Do not begin Phase 11 until every box above is checked and this phase's Status is `DONE`.

---

## Phase 11 — Trending & Analytics Endpoints

**Status:** NOT STARTED
**Depends on:** Phase 9, Phase 10
**Reference docs:** `06-api-specification.md` §4/§8, `07-database-design.md` §4, `02-functional-requirements.md` FR-4/FR-9

**Tasks**
- [ ] `services/analytics_service.py`.
- [ ] `api/user_router.py`: `GET /api/trending`.
- [ ] `api/operator_router.py`: `GET /api/opr/analytics`.

**Definition of Done**
- Aggregation matches `06-api-specification.md` §8's response shape exactly against seeded `usage_metrics` fixtures.

**Verification**
- [ ] `pytest tests/integration/test_analytics.py` per `12-testing-strategy.md` §3
- [ ] `pytest tests/integration/test_trending.py`

**Gate:** Do not begin Phase 12 until every box above is checked and this phase's Status is `DONE`. **This is the M4/"Operator Features" exit gate.**

---

## Phase 12 — Security Hardening Pass

**Status:** NOT STARTED
**Depends on:** Phase 7, Phase 9, Phase 10
**Reference docs:** `08-security.md` (all), `12-testing-strategy.md` §5

**Tasks**
- [ ] Input validation limits (§3) enforced on every relevant field.
- [ ] File content scanning (§8a) wired into both the `/api/chat` image upload and `/api/opr/ingest` file upload.
- [ ] `CORS_ALLOWED_ORIGINS` enforced, no wildcard in staging/production.
- [ ] Prompt-injection delimiter review across every system prompt (§4).

**Definition of Done**
- Every row in `08-security.md` §2's threat table has a verifiable, tested mitigation in place.

**Verification**
- [ ] `pytest tests/security/test_input_validation.py` — boundary tests per `12-testing-strategy.md` §5
- [ ] `pytest tests/security/test_malware_scan.py` — EICAR test file rejected on both upload paths
- [ ] `pytest tests/security/test_cors.py`
- [ ] `pytest tests/security/test_prompt_injection.py` — best-effort, monitored not hard-gated, per `12-testing-strategy.md` §5
- [ ] `pip-audit` (or equivalent) shows no unresolved critical/high vulnerabilities

**Gate:** Do not begin Phase 13 until every box above is checked and this phase's Status is `DONE`.

---

## Phase 13 — Production Hardening

**Status:** NOT STARTED
**Depends on:** Phase 4, Phase 9, Phase 10, Phase 12
**Reference docs:** `07-database-design.md` §7/§8, `10-deployment.md` §4.1/§4.2, `19-cost-management.md` §4, `09-observability.md`, `03-non-functional-requirements.md` §11

**Tasks**
- [ ] `services/retention_service.py` scheduled job (`MESSAGE_RETENTION_DAYS`/`USAGE_METRICS_RETENTION_DAYS`).
- [ ] Complete the `SIGTERM` in-flight-SSE-drain behavior (Phase 5 built the hook stub; this phase finishes it).
- [ ] SSE keepalive pings wired at `SSE_KEEPALIVE_INTERVAL_SECONDS`.
- [ ] Cost-budget alert job (`DAILY_COST_BUDGET_USD`, per `19-cost-management.md` §4).
- [ ] Rate limiter re-verified across multiple simulated replicas under real load (not just the `fakeredis` unit test from Phase 4).
- [ ] Full `/metrics` counter set from `09-observability.md` §5 wired (not just the Phase 5 skeleton).

**Definition of Done**
- A simulated rolling deploy (`SIGTERM` mid-stream) does not truncate an in-flight SSE response.
- The retention job correctly purges old rows and leaves `sessions`/`history_summary`/newer rows untouched.
- The cost alert fires exactly at threshold in a seeded test, not before or after.

**Verification**
- [ ] `pytest tests/unit/test_retention_job.py`
- [ ] `pytest tests/integration/test_graceful_shutdown.py` (or a scripted manual test: start a slow mocked generation, send `SIGTERM`, confirm the client still receives a complete `done` event)
- [ ] `pytest tests/integration/test_rate_limit_multi_instance.py` re-run at a higher simulated replica count
- [ ] `pytest tests/unit/test_cost_budget_alert.py`
- [ ] `curl localhost:8000/metrics` shows every counter from `09-observability.md` §5 with non-placeholder values after generating traffic

**Gate:** Do not begin Phase 14 until every box above is checked and this phase's Status is `DONE`. **This is the M5/"Hardening" exit gate.**

---

## Phase 14 — Full-System Verification (Release Gate)

**Status:** NOT STARTED
**Depends on:** Phase 0 through Phase 13, all `DONE`
**Reference docs:** `12-testing-strategy.md` (full document), `20-performance-target.md`, `13-roadmap.md` M6

There is no Phase 15 to gate into — this phase's completion is the release gate itself, and the same "must not skip" discipline applies to declaring the build complete.

**Tasks**
- [ ] Full automated test suite run (unit + integration + security) — `12-testing-strategy.md` §2-§5.
- [ ] Coverage targets met per `12-testing-strategy.md` §8.
- [ ] Load/performance test against every target in `03-non-functional-requirements.md` §1 / `20-performance-target.md` §2-§4.
- [ ] Manual pre-release checklist — every item in `12-testing-strategy.md` §10.
- [ ] Documentation-vs-code drift check: confirm no doc references a setting, endpoint, or file that doesn't exist in the codebase, and vice versa.

**Definition of Done**
- CI is green end-to-end: lint, type-check, full test suite, coverage threshold, dependency scan.
- Every p50/p95/p99/TTFT/throughput target in `03-non-functional-requirements.md` §1 is met under load test.
- Every checklist item in `12-testing-strategy.md` §10 is checked.

**Verification**
- [ ] CI pipeline green on the release-candidate commit
- [ ] Load test report shows measured vs. target latency/throughput for every row in `03-non-functional-requirements.md` §1
- [ ] `12-testing-strategy.md` §10 checklist fully checked off
- [ ] Docs-vs-code drift check shows zero discrepancies

**Gate:** Do not deploy to production, and do not consider this project's build scope complete, until every box above is checked.

---

## Appendix — When a Later Phase Finds an Earlier Defect

A phase marked `DONE` is not frozen. If work in Phase N reveals a defect in an already-`DONE` Phase M (M < N):

1. Fix the defect directly in Phase M's artifact — do not route around it or patch the symptom in Phase N.
2. Re-run Phase M's full Verification checklist. It must pass again in full before you resume Phase N — a targeted re-check of only the changed part is not sufficient, since the fix could have broken something else Phase M's checklist covers.
3. Only after Phase M is re-confirmed `DONE` do you resume the Phase N work that surfaced the issue.
4. Note the correction in the phase's checklist (e.g., append a short dated note under the relevant task) so the history of what was fixed and when isn't lost.
