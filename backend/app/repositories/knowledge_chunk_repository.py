"""Repository for `knowledge_chunks` — docs/07-database-design.md §3.5."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.knowledge_chunk import KnowledgeChunk
from app.repositories.base import BaseRepository


class KnowledgeChunkRepository(BaseRepository[KnowledgeChunk]):
    model = KnowledgeChunk

    async def bulk_create(self, chunks: list[KnowledgeChunk]) -> list[KnowledgeChunk]:
        self._session.add_all(chunks)
        await self._session.flush()
        return chunks

    async def count_by_document_id(self, document_id: uuid.UUID) -> int:
        """Chunk count for a document, read before a cascading delete
        (docs/06-api-specification.md §7.1's `chunks_removed` response field)."""
        result = await self._session.execute(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document_id)
        )
        return result.scalar_one()
