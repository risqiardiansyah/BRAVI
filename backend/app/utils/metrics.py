"""Prometheus metric objects shared across modules — docs/09-observability.md §5.

Counters/histograms/gauges register into `prometheus_client`'s default global registry
as soon as this module is imported; `GET /metrics` (`app/api/system_router.py`) exposes
whatever is registered at scrape time via `generate_latest()`. Metrics are added
progressively as each phase's code path is built (docs/IMPLEMENTATION_PLAN.md Phase 5
note) — this module now holds the full set from `09-observability.md` §5 (Phase 13).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

ingestion_jobs_total = Counter(
    "ingestion_jobs_total",
    "Ingestion jobs processed, labeled by final status.",
    ["status"],
)

ingestion_job_duration_ms = Histogram(
    "ingestion_job_duration_ms",
    "Ingestion graph run duration in milliseconds, per document.",
)

daily_cost_budget_exceeded = Gauge(
    "daily_cost_budget_exceeded",
    "1 if today's total estimated_cost_usd has reached DAILY_COST_BUDGET_USD, else 0 "
    "(0 when no budget is configured) — docs/19-cost-management.md §4.",
)

chat_requests_total = Counter(
    "chat_requests_total",
    "Chat turns completed, labeled by persona and short-circuit tier ('none' when a "
    "full RAG generation ran).",
    ["persona", "short_circuit_reason"],
)

chat_latency_ms = Histogram(
    "chat_latency_ms",
    "End-to-end chat turn latency in milliseconds, labeled by endpoint and whether the "
    "turn was short-circuited.",
    ["endpoint", "short_circuited"],
)

chat_ttft_ms = Histogram(
    "chat_ttft_ms",
    "Time to first streamed token in milliseconds, full-RAG path only "
    "(docs/03-non-functional-requirements.md §1 TTFT target), labeled by endpoint.",
    ["endpoint"],
)

bedrock_embedding_calls_total = Counter(
    "bedrock_embedding_calls_total",
    "Bedrock embedding invocations attempted (docs/14-bedrock-integration.md §2).",
)

bedrock_text_calls_total = Counter(
    "bedrock_text_calls_total",
    "Bedrock text-generation invocations attempted (docs/14-bedrock-integration.md §2).",
)

bedrock_tokens_total = Counter(
    "bedrock_tokens_total",
    "Bedrock tokens processed, labeled by direction ('input'/'output').",
    ["direction"],
)

estimated_cost_usd_total = Counter(
    "estimated_cost_usd_total",
    "Cumulative estimated Bedrock spend in USD (docs/19-cost-management.md §2).",
)

rate_limit_rejections_total = Counter(
    "rate_limit_rejections_total",
    "Requests rejected with 429 by the Redis-backed rate limiter, labeled by endpoint.",
    ["endpoint"],
)

bedrock_circuit_breaker_state = Gauge(
    "bedrock_circuit_breaker_state",
    "0=closed, 1=open, 2=half_open (docs/11-coding-standard.md §12).",
)

knowledge_documents_deleted_total = Counter(
    "knowledge_documents_deleted_total",
    "DELETE /api/opr/knowledge/{id} calls that removed a document "
    "(docs/08-security.md §7 — destructive-action visibility).",
)
