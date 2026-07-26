# 09 — Observability

## 1. Goals

Provide visibility into AI usage (cost, latency, model used), system health, and question trends, sufficient to power `/api/opr/analytics` and support operational debugging.

## 2. What Gets Logged/Measured Per AI-Involved Request

Persisted to `usage_metrics` (see `07-database-design.md`) and emitted as structured logs:

| Field | Description |
|---|---|
| `session_id`, `user_id`, `persona` | Request identity |
| `endpoint` | Which route handled the request |
| `question` | Normalized question text |
| `short_circuited`, `short_circuit_reason` | Which tier (if any) short-circuited the request — `'greeting' \| 'out_of_topic' \| 'low_similarity' \| 'add_knowledge_intent'` (Operator-only) `\| null` |
| `similarity_best_score` | Top pgvector match score |
| `model_embedding_used`, `model_text_used` | Bedrock model IDs actually invoked (or null if skipped) |
| `input_tokens`, `output_tokens` | From Bedrock response metadata where available |
| `estimated_cost_usd` | Computed from token counts × known Bedrock pricing table (configurable) |
| `latency_ms` | End-to-end and per-node breakdown |
| `created_at` | Timestamp |

## 3. Structured Logging

- JSON log format, one line per event, including a `request_id`/`trace_id` correlating all log lines for a single request.
- Log levels: `DEBUG` (dev only, includes prompt content), `INFO` (normal operation), `WARNING` (retries, degraded), `ERROR` (failures).
- Never log raw AWS credentials; redact if accidentally present in error payloads.

## 4. Per-Node Latency Breakdown (Chat Graph)

Each LangGraph node execution time recorded and summed:
```json
{
  "request_id": "uuid",
  "node_latencies_ms": {
    "preprocess_input": 12,
    "classify_greeting": 3,
    "classify_out_of_topic": 8,
    "embed_question": 220,
    "similarity_search": 15,
    "condense_history": 0,
    "generate_answer": 3100
  },
  "total_ms": 3358
}
```

## 5. Metrics to Export

| Metric | Type | Notes |
|---|---|---|
| `chat_requests_total` | Counter | Labeled by `persona`, `short_circuit_reason` |
| `chat_latency_ms` | Histogram | Labeled by `endpoint`, `short_circuited` |
| `bedrock_embedding_calls_total` | Counter | |
| `bedrock_text_calls_total` | Counter | |
| `bedrock_tokens_total` | Counter | Labeled by `input`/`output` |
| `estimated_cost_usd_total` | Counter | |
| `ingestion_jobs_total` | Counter | Labeled by `status` |
| `ingestion_job_duration_ms` | Histogram | |
| `rate_limit_rejections_total` | Counter | Labeled by `endpoint`; requests rejected with `429` by the Redis-backed limiter |
| `bedrock_circuit_breaker_state` | Gauge | `0`=closed, `1`=open, `2`=half-open — see `11-coding-standard.md` §12 |
| `knowledge_documents_deleted_total` | Counter | Incremented by `DELETE /api/opr/knowledge/{id}` — destructive-action visibility (`08-security.md` §7) |

Exported via `GET /metrics` (Prometheus-compatible, e.g. `prometheus-fastapi-instrumentator`) — see `06-api-specification.md` §9.3. If no monitoring stack is available yet, the `usage_metrics` table queried by `/api/opr/analytics` remains the Phase-1 fallback for the chat/ingestion-specific counters (not the infra-level ones like circuit-breaker state).

## 6. Dashboards (recommended, Phase 2)

- AI cost over time (daily/weekly).
- Short-circuit rate (% of requests avoiding LLM calls) — key cost-efficiency KPI from `01-prd.md`.
- Latency percentiles by endpoint.
- Top questions (User vs Visitor/Operator).
- Ingestion job success/failure trend.

## 7. Alerting (recommended, Phase 2)

| Condition | Alert |
|---|---|
| Bedrock error rate > X% over 5 min | Page/notify on-call |
| p95 latency > threshold sustained | Notify |
| Ingestion startup job fails entirely | Notify |
| Daily estimated cost exceeds budget threshold (`DAILY_COST_BUDGET_USD`, see `19-cost-management.md` §4) | Notify |

## 8. Health Checks

Formal request/response contract lives in `06-api-specification.md` §9 — summary:

- `GET /health` (liveness) — process up.
- `GET /health/ready` (readiness) — DB connection OK, Redis reachability OK, Bedrock reachability check (lightweight, not a full inference call). Returns `503` if any check fails; the deploy/orchestrator must not route traffic in that state (see `10-deployment.md` §4).

## 9. Tracing (optional, Phase 2)

- Consider OpenTelemetry instrumentation across FastAPI routes and LangGraph node executions for distributed tracing if the system grows beyond a monolith.
