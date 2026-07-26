# 03 — Non-Functional Requirements (NFRs)

## 1. Performance

| Requirement | Target |
|---|---|
| Short-circuit response latency (greeting/out-of-topic/low-similarity) | < 500ms p95 / < 1,500ms p99 |
| Full RAG response latency (retrieval + condensation + generation) | < 6s p95 / < 12s p99 (Bedrock-dependent) |
| Time to First Token (TTFT), full RAG path | < 2.5s p95 — see `20-performance-target.md` §3 for why this is tracked separately from total latency |
| Sustained aggregate throughput | ≥ 20 requests/sec across all replicas at Phase 1 concurrency target |
| Ingestion throughput | Configurable batch size; must not block API request threads (async/background) |
| Concurrent chat sessions | Support at least 100 concurrent sessions in Phase 1 (tune per infra) |

Full rationale, per-node latency budget, and concurrency-scaling guidance behind the p99/TTFT/throughput rows above: `20-performance-target.md`.

## 2. Scalability

- Backend must be stateless per-request (session state lives in PostgreSQL, not in-process memory) to allow horizontal scaling of API instances. The one cross-cutting exception is rate-limit counters, which must live in Redis (shared across replicas) rather than in-process — an in-memory limiter silently stops working correctly the moment there's more than one replica (`08-security.md` §6).
- Postgres connection pool (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`) must be sized so `pool size × replica count` stays under the database's `max_connections`, and re-checked whenever autoscaling limits change.
- pgvector index (HNSW) must be chosen/tuned for the expected knowledge base size; `PGVECTOR_HNSW_EF_SEARCH` is a runtime (no-reindex) recall/latency tuning knob, tuned separately from the build-time index parameters as the knowledge base grows.
- Ingestion background jobs should be queue-based (or async task) to scale independently from the request-serving path, bounded by `INGESTION_CONCURRENCY`/`EMBEDDING_BATCH_SIZE` to avoid saturating Bedrock's account-level throughput limits.

## 3. Availability & Reliability

- Target uptime: 99.5% (Phase 1, single-region).
- Startup ingestion job failures on individual documents must not crash the application; failures are logged and retryable.
- Bedrock call failures must have retry-with-backoff (bounded retries via `BEDROCK_MAX_RETRIES`/`BEDROCK_RETRY_BACKOFF_BASE_MS`, timeout via `BEDROCK_TIMEOUT_SECONDS`) and a graceful fallback error response to the user; a circuit breaker in `clients/bedrock_client.py` must fail fast during a sustained Bedrock outage rather than letting requests queue up and cascade (`11-coding-standard.md` §12).
- Rolling deploys must drain in-flight SSE streams on `SIGTERM` rather than hard-killing them (`10-deployment.md` §4.1).

## 4. Security

- All secrets/config via `.env` — never hard-coded.
- Input validation & sanitization on all endpoints (payload size limits, file type/size checks).
- Protection against prompt injection: system prompts must clearly separate instructions from retrieved content and user input; retrieved document content is treated as untrusted data, not instructions.
- File upload scanning: restrict accepted MIME types (PDF, common image types), enforce max upload size, scan content for malware.
- CORS restricted to known frontend origin(s) via `CORS_ALLOWED_ORIGINS`; no wildcard in staging/production.
- No PII should be logged in plaintext where avoidable; analytics data should be aggregated.
- Full detail in `08-security.md`.

## 5. Cost Efficiency

- Minimize Bedrock invocations via the short-circuit pipeline defined in `02-functional-requirements.md` FR-6.
- Track and report token/cost usage per request to support ongoing cost optimization (see `09-observability.md`).
- Batch embedding calls during ingestion where possible instead of per-chunk calls.

## 6. Maintainability

- Follow conventions in `11-coding-standard.md`.
- Modular LangGraph graph design: separate graphs/nodes for chat vs. ingestion (see `05-ai-agent-design.md`) — avoid a single monolithic graph.
- Config centralized via `.env` + a typed settings module (e.g., Pydantic `BaseSettings`).

## 7. Observability

- Structured (JSON) logs for every AI-involved request: latency, model, tokens, short-circuit tier, session/user id.
- Metrics exported in a form consumable by a monitoring stack (e.g., Prometheus-compatible or DB-backed dashboards feeding `/api/opr/analytics`).
- Full detail in `09-observability.md`.

## 8. Data Retention & Privacy

- Session/message retention: configurable via `MESSAGE_RETENTION_DAYS` (default `90`); `usage_metrics` retention via `USAGE_METRICS_RETENTION_DAYS` (default `180`), enforced by a scheduled cleanup job — see `07-database-design.md` §7.
- Ingested knowledge documents retained indefinitely unless explicitly removed by an operator (delete endpoint may be added in a later phase).

## 9. Portability / Deployment

- Must run in containerized form (Docker) for consistent deployment across environments.
- Must not hard-depend on any non-Bedrock LLM/embedding provider.
- Full detail in `10-deployment.md`.

## 10. Compliance

- No specific regulatory compliance target defined for Phase 1 (to be revisited if handling regulated data).

## 11. Streaming (SSE) Requirements

- `/api/chat`/`/api/opr/chat` responses are long-lived SSE connections, not typical short REST calls — this has infrastructure implications beyond the API contract itself:
  - Server must emit a keepalive ping (`SSE_KEEPALIVE_INTERVAL_SECONDS`) during long generations so idle-timeout infrastructure doesn't kill the connection mid-answer.
  - Load balancer/reverse-proxy idle timeout must be configured to exceed the keepalive interval with margin (`10-deployment.md` §4.2), and proxy response buffering must be disabled for these routes.
  - Rolling deploys must drain in-flight streams rather than hard-killing them (`10-deployment.md` §4.1).
