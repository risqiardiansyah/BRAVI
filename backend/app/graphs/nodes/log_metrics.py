"""`log_metrics` node — docs/05-ai-agent-design.md §3.2, docs/09-observability.md §3/§5.

Always the last node, on both the success and failure paths: one structured log line
plus the ingestion-specific Prometheus counters (`app/utils/metrics.py`).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.graphs.ingestion_state import IngestionState
from app.utils.metrics import ingestion_job_duration_ms, ingestion_jobs_total

logger = logging.getLogger(__name__)


async def log_metrics(state: IngestionState) -> dict[str, Any]:
    status = state.get("status") or "failed"

    duration_ms: float | None = None
    started = state.get("started_monotonic")
    if started is not None:
        duration_ms = (time.monotonic() - started) * 1000
        ingestion_job_duration_ms.observe(duration_ms)
    ingestion_jobs_total.labels(status=status).inc()

    logger.info(
        "ingestion_job_finished",
        extra={
            "document_id": str(state.get("document_id")),
            "job_id": str(state.get("job_id")),
            "status": status,
            "chunk_count": len(state.get("chunks", [])),
            "duration_ms": duration_ms,
            "error": state.get("error"),
        },
    )
    return {}
