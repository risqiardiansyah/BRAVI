"""Repository for `ingestion_jobs` — docs/07-database-design.md §3.6."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.ingestion_job import IngestionJob
from app.repositories.base import BaseRepository


class IngestionJobRepository(BaseRepository[IngestionJob]):
    model = IngestionJob

    async def mark_completed(self, job: IngestionJob) -> None:
        job.status = "completed"
        job.completed_at = datetime.now(UTC)
        await self._session.flush()

    async def mark_failed(self, job: IngestionJob, *, error_message: str) -> None:
        job.status = "failed"
        job.error_message = error_message
        job.completed_at = datetime.now(UTC)
        await self._session.flush()
