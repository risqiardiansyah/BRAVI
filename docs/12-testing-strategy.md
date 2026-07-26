# 12 — Testing Strategy

## 1. Test Levels

| Level | Scope | Tooling |
|---|---|---|
| Unit | Individual functions/nodes/repositories | `pytest`, `unittest.mock` |
| Integration | Router → service → graph → DB (test DB), Bedrock mocked | `pytest`, `pytest-asyncio`, `httpx.AsyncClient`, test PostgreSQL (`pgvector` enabled) |
| End-to-End (E2E) | Full request/response against a running instance in `staging` | `pytest` + real HTTP calls, or Postman/Newman collection |
| Load/Performance | Latency/throughput under concurrent load | `locust` or `k6` |

## 2. Unit Testing Focus Areas

- **Short-circuit classifiers**: greeting detection, out-of-topic detection, and `classify_add_knowledge_intent` (Operator only — bilingual trigger phrases, e.g. "tambah knowledge ai", "add ai knowledge") — table-driven tests with representative positive/negative examples.
- **Similarity threshold logic**: boundary tests around `SIMILARITY_SCORE_THRESHOLD`.
- **Chunking logic**: verify chunks respect `CHUNK_SIZE_TOKENS`/`CHUNK_OVERLAP_TOKENS` on sample texts using the actual tokenizer (not a character-count approximation — see `18-rag-design.md` §3), and that config validation rejects `CHUNK_OVERLAP_TOKENS >= CHUNK_SIZE_TOKENS`.
- **Cost/token estimation math**: verify `estimated_cost_usd` calculation given mocked token counts, using the correct per-model rate row from the pricing table (`19-cost-management.md` §2) for whichever model was invoked.
- **Repositories**: CRUD correctness against a test database.
- **Bedrock client wrapper**: request/response shape handling, timeout/retry-with-backoff behavior, error-taxonomy classification (retryable vs. non-retryable per `14-bedrock-integration.md` §5), and circuit-breaker state transitions against the configured `BEDROCK_CIRCUIT_BREAKER_FAILURE_THRESHOLD`/`BEDROCK_CIRCUIT_BREAKER_COOLDOWN_SECONDS` (open after threshold consecutive failures → fails fast → half-open probe after cooldown) — all against a mocked `boto3` client, per `11-coding-standard.md` §12 / `14-bedrock-integration.md` §6.
- **Rate limiter**: token-bucket math against a mocked/fakeredis Redis client; assert limits are enforced per `user_id`/IP and correctly shared (not per-process).
- **Retention job**: purges `messages`/`usage_metrics` older than `MESSAGE_RETENTION_DAYS`/`USAGE_METRICS_RETENTION_DAYS`, leaves newer rows, `sessions` rows, and `sessions.history_summary` untouched.
- **Idempotency-Key conflict**: the same `Idempotency-Key` reused with different content-hash returns `409`/`IDEMPOTENCY_KEY_CONFLICT` (`22-error-handling.md` §4) without performing a second ingestion.

## 3. Integration Testing Focus Areas

- `user_chat_graph` and `operator_chat_graph` full run for each short-circuit tier (greeting / out-of-topic / low-similarity / full RAG), asserting correct routing, that Bedrock text-generation is **not** invoked for the first three tiers (critical cost-control regression test), that streamed output assembles into valid Markdown **in Bahasa Indonesia** with a trailing `## Sources` section, and — for an artificially slow mocked generation call — that a keepalive comment ping is emitted at `SSE_KEEPALIVE_INTERVAL_SECONDS`.
- Add-knowledge-intent regression test: `operator_chat_graph` returns the exact fixed template (`Silahkan klik tombol berikut untuk mengisi form: <BTN>Add Knowledge</BTN>`) with `short_circuited: true`/`short_circuit_reason: "add_knowledge_intent"`/`mode: null` and **no Bedrock call at all**; the same question sent to `user_chat_graph` via `/api/chat` must fall through to normal QA handling (never the template) — this is also a persona-isolation check, not just a functional one.
- Response-language regression test: assert generated answers (mocked Bedrock response) and every canned response are the documented Bahasa Indonesia text, for both an Indonesian-phrased and an English-phrased input question.
- Answer freshness/versioning: retrieved chunk with `valid_until` in the past or `superseded_by_document_id` set → prompt context includes that metadata and (with a mocked model response) the answer mentions it; retrieved chunk with neither set → assert the context passed to the model contains no freshness metadata for that chunk (nothing to fabricate from).
- Persona tool-isolation regression test: assert `user_chat_graph`'s module has no import path reaching `tools/operator_tools.py` (see `11-coding-standard.md` §8.1).
- `ingestion_graph` full run: file ingestion, text ingestion, and failure path (corrupt PDF, unreachable URL).
- Session + message persistence and retrieval via `/api/messages`; `sessions.title` correctly set from the first user message and never overwritten on subsequent messages.
- History condensation persistence: a session exceeding `CONTEXT_CONDENSATION_MAX_TURNS` gets `sessions.history_summary`/`history_summary_updated_at` populated; a subsequent triggering request re-summarizes only messages added since `history_summary_updated_at` (incremental, not from-scratch) — see `17-memory-strategy.md` §4.
- Session resolution regression test: empty `session_id` auto-creates; unknown provided `session_id` returns `404`; existing `session_id` continues that session's history.
- `/api/opr/analytics` aggregation correctness against seeded `usage_metrics` fixtures.
- Startup ingestion job idempotency: running it twice should not duplicate `knowledge_documents` for unchanged sources.
- `/api/opr/ingest` `Idempotency-Key`/content-hash: a retried request with the same key/content does not create a second `knowledge_documents` row or a second embedding job; `supersedes_document_id` correctly sets `superseded_by_document_id` on the old document.
- `DELETE /api/opr/knowledge/{id}`: chunks/vectors removed and immediately excluded from `similarity_search`; `ingestion_jobs.document_id` set to `NULL` rather than the job row being deleted; startup-managed document resets `knowledge_sources.is_ingested = false`; re-deleting the same id (or an unknown id) returns `404`.
- `GET /health`, `GET /health/ready`, `GET /metrics`: readiness correctly reports `503` when DB or Redis is unreachable; metrics endpoint exposes the counters listed in `09-observability.md` §5.
- Error code registry: spot-check that each documented failure path returns the exact `code` from `22-error-handling.md` §2 (e.g., unknown `session_id` → `SESSION_NOT_FOUND`, open circuit breaker → `BEDROCK_UNAVAILABLE`) rather than an ad hoc string.
- Cost budget alert: with `DAILY_COST_BUDGET_USD` set low in a test environment, seeded `usage_metrics` rows summing past it trigger the alert path (`19-cost-management.md` §4); unset `DAILY_COST_BUDGET_USD` never fires it.

## 4. Contract/API Testing

- Validate every endpoint in `06-api-specification.md` against its documented request/response schema (e.g., via Pydantic schema validation in tests, or an OpenAPI-diff check in CI).

## 5. Security Testing

- Input validation boundary tests (oversized payloads, invalid MIME types, path traversal attempts in ingestion `relative_path`).
- Prompt-injection regression tests: seed a malicious instruction inside a test knowledge chunk and assert the model does not follow it (best-effort, non-deterministic — track as a monitored test, not a hard gate).
- Upload malware-scan path: assert a known-bad test payload (EICAR test file) is rejected on both `/api/chat` image upload and `/api/opr/ingest`.
- CORS: assert disallowed origins are rejected and `CORS_ALLOWED_ORIGINS` origins are accepted.
- Rate limiting: assert requests beyond `RATE_LIMIT_REQUESTS_PER_MINUTE`/`RATE_LIMIT_BURST` return `429`, and that the limit is enforced across simulated multiple app instances sharing one Redis (not per-process).
- Dependency vulnerability scan (`pip-audit`) as a CI gate.

## 6. Performance Testing

- Baseline load test: N concurrent chat sessions, measure p50/p95 latency per short-circuit tier and full RAG path.
- Ingestion throughput test: batch of sample PDFs, measure total time and failure rate.

## 7. Test Data & Fixtures

- Seed a small, fixed "test knowledge base" (a few sample PDFs/text) used consistently across integration tests for reproducible retrieval results.
- Mocked Bedrock responses stored as fixtures (recorded once, replayed in CI) to avoid real API cost/flakiness in every CI run; a smaller subset of tests may run against real Bedrock in a nightly/manual pipeline.

## 8. Coverage Targets (initial)

| Area | Target |
|---|---|
| Services/repositories | ≥ 80% line coverage |
| Graph node logic | ≥ 80% line coverage |
| Routers (thin layer) | Covered via integration tests |

## 9. CI Gates

1. Lint + type-check must pass.
2. Unit + integration tests must pass.
3. Coverage threshold must be met.
4. Dependency vulnerability scan must show no critical/high unresolved issues.

## 10. Manual/Exploratory Testing (pre-release checklist)

- [ ] Greeting message returns instantly with no Bedrock text call (verify via logs/metrics).
- [ ] Out-of-topic question returns canned response before generation.
- [ ] Low-similarity question returns "no knowledge found" with no generation call.
- [ ] In-domain question returns grounded, cited answer, in Bahasa Indonesia, even when asked in English.
- [ ] Image upload processed correctly on `/api/chat`.
- [ ] Operator summary-mode question returns a structured summary.
- [ ] Operator asking "tambah knowledge ai" (and an English variant like "add ai knowledge") gets the exact `<BTN>Add Knowledge</BTN>` template, instantly, with no Bedrock call; the same phrase sent to `/api/chat` as a User does **not** trigger it.
- [ ] Ingesting a document with `valid_until` in the past, then asking a question it answers, produces an answer that mentions it's potentially outdated; ingesting a document with neither `valid_until` nor `supersedes_document_id` produces an answer with no such caveat.
- [ ] `/api/opr/ingest` with file and with raw text both succeed.
- [ ] `/api/opr/knowledge` reflects newly ingested items, including freshness/versioning fields.
- [ ] `DELETE /api/opr/knowledge/{id}` removes a document from a subsequent `/api/chat` answer immediately (ask the same question before and after deleting).
- [ ] `/api/opr/analytics` numbers match manually verified sample data.
- [ ] Startup ingestion job runs once and is not re-triggered on restart.
- [ ] `GET /health` and `GET /health/ready` respond correctly, including `/health/ready` returning `503` with DB or Redis stopped.
- [ ] Sending `session_id` omitted vs. a fresh valid UUID (unknown to the DB) vs. an existing `session_id` all behave per the documented resolution rule (auto-create / `404` / continue).
- [ ] A rolling deploy/restart during an in-flight chat response does not abruptly cut the SSE stream (graceful shutdown drains it).
