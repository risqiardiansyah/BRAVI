"""Repository for `knowledge_sources` — docs/07-database-design.md §3.3."""

from __future__ import annotations

from sqlalchemy import select

from app.models.knowledge_source import KnowledgeSource
from app.repositories.base import BaseRepository


class KnowledgeSourceRepository(BaseRepository[KnowledgeSource]):
    model = KnowledgeSource

    async def get_by_relative_path(self, relative_path: str) -> KnowledgeSource | None:
        result = await self._session.execute(
            select(KnowledgeSource).where(KnowledgeSource.relative_path == relative_path)
        )
        return result.scalar_one_or_none()

    async def mark_ingested(self, source: KnowledgeSource, *, content_hash: str) -> None:
        """Idempotency bookkeeping — docs/07-database-design.md §5. Only called on a
        successful ingestion; a failed attempt leaves `is_ingested`/`content_hash`
        unchanged so the next run retries it."""
        source.is_ingested = True
        source.content_hash = content_hash
        await self._session.flush()

    async def reset_ingested(self, source: KnowledgeSource) -> None:
        """Reverts a source to not-yet-ingested — docs/07-database-design.md §5a: when a
        startup-managed document is deleted via `DELETE /api/opr/knowledge/{id}`, its
        source row is reset so the *next* startup ingestion run re-ingests it (a
        documented trade-off, not a bug — see §5a)."""
        source.is_ingested = False
        await self._session.flush()
