"""Scheduled data-retention cleanup — docs/07-database-design.md §7,
docs/03-non-functional-requirements.md §8.

Purges `messages` older than `MESSAGE_RETENTION_DAYS` and `usage_metrics` older than
`USAGE_METRICS_RETENTION_DAYS`. `sessions` rows are left in place (only their
`messages` are pruned) so `GET /api/session` history isn't silently truncated to zero
(docs/07-database-design.md §7). Invoked by `app/jobs/retention_scheduler.py` at each
occurrence of `RETENTION_CRON_SCHEDULE`, mirroring the ingestion job's cron pattern
(docs/IMPLEMENTATION_PLAN.md Phase 13's dated note).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.config import settings
from app.db import AsyncSessionLocal
from app.repositories.message_repository import MessageRepository
from app.repositories.usage_metric_repository import UsageMetricRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetentionResult:
    messages_deleted: int
    usage_metrics_deleted: int


async def run_retention_cleanup() -> RetentionResult:
    """Runs one retention pass. A plain indexed `DELETE ... WHERE created_at < cutoff`
    is sufficient at Phase-1 launch volume — docs/07-database-design.md §8 names
    time-based partitioning as the scale-out path once this starts causing vacuum
    pressure, not required here."""
    now = datetime.now(UTC)
    message_cutoff = now - timedelta(days=settings.MESSAGE_RETENTION_DAYS)
    usage_metric_cutoff = now - timedelta(days=settings.USAGE_METRICS_RETENTION_DAYS)

    async with AsyncSessionLocal() as session:
        messages_deleted = await MessageRepository(session).delete_older_than(message_cutoff)
        usage_metrics_deleted = await UsageMetricRepository(session).delete_older_than(
            usage_metric_cutoff
        )
        await session.commit()

    logger.info(
        "retention cleanup job finished",
        extra={
            "messages_deleted": messages_deleted,
            "usage_metrics_deleted": usage_metrics_deleted,
            "message_cutoff": message_cutoff.isoformat(),
            "usage_metric_cutoff": usage_metric_cutoff.isoformat(),
        },
    )
    return RetentionResult(
        messages_deleted=messages_deleted, usage_metrics_deleted=usage_metrics_deleted
    )
