# 13 — Roadmap

## Phase 1 — Foundations (M1)
- Repo scaffolding per `11-coding-standard.md` structure.
- `.env` / config module setup, including the full var set in `10-deployment.md` §3 (not just DB/Bedrock — retrieval, resilience, rate-limit, retention, CORS, SSE knobs too), with startup validation (e.g., `CHUNK_OVERLAP_TOKENS < CHUNK_SIZE_TOKENS`).
- PostgreSQL + `pgvector` provisioning, base schema migration (`07-database-design.md`).
- Redis provisioning (docker-compose locally; managed Redis in staging/production — see `10-deployment.md` §6).
- `clients/bedrock_client.py` with timeout/retry-with-backoff/circuit breaker (`11-coding-standard.md` §12); AWS Bedrock connectivity smoke test (embedding + text call) through it.
- `GET /health`, `GET /health/ready` implemented (DB + Redis + Bedrock reachability).
- CI pipeline skeleton (lint, type-check, test, build).

**Exit criteria**: App boots, connects to DB and Redis, successfully calls Bedrock for both embedding and text generation in a smoke test, `/health`/`/health/ready` report correctly.

## Phase 2 — Ingestion Pipeline (M2)
- `knowledge_sources`, `knowledge_documents` (incl. `valid_until`, `superseded_by_document_id`), `knowledge_chunks`, `ingestion_jobs` tables finalized (confirm embedding vector dimension against the actual `embed-v4` output — `01-prd.md` §11 risk #4).
- `ingestion_graph` implemented (load → extract → chunk → embed → store), batched/concurrency-bounded via `EMBEDDING_BATCH_SIZE`/`INGESTION_CONCURRENCY`.
- Startup one-time ingestion job (`DOCUMENT_BASE_URL`-based) with idempotency.
- `POST /api/opr/ingest` (file + text) implemented as async/background, honoring `Idempotency-Key`/content-hash to prevent double-ingestion on client retry; accepts optional `valid_until`/`supersedes_document_id`.
- `GET /api/opr/knowledge` implemented (incl. freshness/versioning fields).
- `DELETE /api/opr/knowledge/{id}` implemented (cascade delete + `knowledge_sources.is_ingested` reset — `07-database-design.md` §5a).

**Exit criteria**: Sample documents ingest successfully end-to-end (startup job + manual endpoint); duplicate startup runs and duplicate `/api/opr/ingest` retries don't duplicate data; deleting a document removes it from retrieval immediately.

## Phase 3 — Core Chat Pipeline (M3)
- `sessions` (incl. `title`) / `messages` tables + `GET /api/session`, `POST /api/messages`. No `POST /api/session` — sessions are created implicitly by `/api/chat`/`/api/opr/chat` (empty `session_id` → auto-create; unknown provided `session_id` → `404`).
- `user_chat_graph` / `operator_chat_graph` implemented with full short-circuit pipeline (greeting → out-of-topic → similarity threshold → RAG), each with its own isolated tool registry (see `11-coding-standard.md` §8.1), retrieval bounded by `RETRIEVAL_TOP_K`/`SUMMARY_TOP_K` and generation bounded by `BEDROCK_MAX_OUTPUT_TOKENS`.
- Canonical system prompts implemented **in Bahasa Indonesia** exactly as specified in `docs/prompts/ai-agent.md` (not a placeholder/English draft to translate "later") — including the conditional freshness/versioning instruction.
- Contextual condensation logic.
- `POST /api/chat` fully functional (text + image via Bedrock multimodal), streamed as SSE with keepalive pings (`SSE_KEEPALIVE_INTERVAL_SECONDS`), Markdown answers in Bahasa Indonesia with appended `[Link Text](URL)` sources.
- `usage_metrics` logging wired into every chat request.

**Exit criteria**: All checklist items in `12-testing-strategy.md` §10 (chat-related) pass manually; short-circuit tiers verified to skip Bedrock text calls; a slow mocked generation confirms keepalive pings keep the stream alive; spot-checked answers confirmed in Bahasa Indonesia regardless of question language.

## Phase 4 — Operator Features (M4)
- `POST /api/opr/chat` with summary-mode routing.
- `classify_add_knowledge_intent` implemented in `operator_chat_graph` only, returning the fixed `<BTN>Add Knowledge</BTN>` template; regression test confirms `/api/chat` never returns it.
- `GET /api/opr/analytics` (top questions, volume, latency, model usage, cost).
- `GET /api/trending`.

**Exit criteria**: Operator can converse, summarize, get redirected to the add-knowledge form on request, list/delete knowledge, and view meaningful analytics from real usage data generated in Phase 3 testing.

## Phase 5 — Hardening (M5)
- Security review per `08-security.md`: input validation, prompt-injection tests, upload malware scanning, CORS policy populated.
- Redis-backed rate limiting (`middleware/rate_limit.py`) live on `/api/chat`, `/api/opr/chat`, `/api/opr/ingest`, verified to enforce correctly across multiple replicas (not just single-process).
- `GET /metrics` wired to a scrape target (or `/api/opr/analytics` used as the Phase-1 fallback per `09-observability.md` §5).
- Graceful shutdown (SSE stream draining on `SIGTERM`) and LB/proxy idle-timeout alignment with `SSE_KEEPALIVE_INTERVAL_SECONDS` verified in a real rolling-deploy test (`10-deployment.md` §4.1/§4.2).
- `retention_service` scheduled job live (`MESSAGE_RETENTION_DAYS`/`USAGE_METRICS_RETENTION_DAYS`).
- Load testing and latency tuning, including Bedrock circuit-breaker behavior under simulated throttling.
- Deployment to staging via `10-deployment.md`.

**Exit criteria**: Staging environment passes security checklist, load test targets met, rate limiting verified correct across replicas, dashboards/alerts (if in scope) operational.

## Phase 6 — Release & Handover (M6)
- Documentation finalized (all `docs/00-23` + `prompts/`).
- Production deployment.
- Handover session with operators (knowledge ingestion workflow, analytics usage).

**Exit criteria**: Production live, monitored, documented, operator team onboarded.

---

## Backlog / Future Phases (Phase 2 of the product, not this build)

- Authentication & role-based access control (User vs Operator enforced server-side).
- `PATCH /api/opr/knowledge/{id}` to update `valid_until`/`superseded_by_document_id` on an already-ingested document without re-ingesting.
- Endpoint to remove/deactivate a `knowledge_sources` row directly, so a deleted startup-managed document doesn't get silently re-ingested on the next startup run (`07-database-design.md` §5a).
- Semantic clustering for trending questions (beyond exact/normalized text match).
- Content moderation on input/output.
- Multi-region / multi-tenant support.
- Semantic/response caching layer for repeated common questions (beyond Redis rate-limit usage).
- Alerting & dashboarding stack (Grafana/Prometheus) if not done in Phase 5.
- Distributed tracing (OpenTelemetry).
- Read replica for `/api/opr/analytics` to isolate reporting load from chat write traffic.
