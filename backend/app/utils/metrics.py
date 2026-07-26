"""Prometheus metric objects shared across modules — docs/09-observability.md §5.

Counters/histograms register into `prometheus_client`'s default global registry as
soon as this module is imported; `GET /metrics` (`app/api/system_router.py`) exposes
whatever is registered at scrape time via `generate_latest()`. Metrics are added
progressively as each phase's code path is built (docs/IMPLEMENTATION_PLAN.md Phase 5
note) — this module currently holds the ingestion-job metrics Phase 6 introduces.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

ingestion_jobs_total = Counter(
    "ingestion_jobs_total",
    "Ingestion jobs processed, labeled by final status.",
    ["status"],
)

ingestion_job_duration_ms = Histogram(
    "ingestion_job_duration_ms",
    "Ingestion graph run duration in milliseconds, per document.",
)
