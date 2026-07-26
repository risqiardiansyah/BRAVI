"""Repository for `knowledge_chunks` — docs/07-database-design.md §3.5."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.repositories.base import BaseRepository


@dataclass(frozen=True)
class SimilarityMatch:
    """One row of the retrieval query — docs/18-rag-design.md §4."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    page_number: int | None
    score: float
    title: str | None
    source_url: str | None
    valid_until: date | None
    superseded_by_title: str | None


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

    async def similarity_search(
        self, query_embedding: list[float], *, top_k: int
    ) -> list[SimilarityMatch]:
        """Top-`top_k` nearest chunks by cosine distance, joined with their document's
        citation/freshness fields in one pass — docs/18-rag-design.md §4's exact query
        shape, extended with a second join to resolve `superseded_by_title` (the doc's
        raw SQL only selects `superseded_by_document_id`; the chat graph/API need the
        superseding document's current title, not just its id — docs/06-api-specification.md
        §0's `sources[].superseded_by_title`).

        `<=>` is pgvector's cosine-DISTANCE operator (ascending = most similar first);
        `score` is `1 - distance`, compared against `SIMILARITY_SCORE_THRESHOLD` by
        `check_similarity_threshold`.
        """
        superseding_document = aliased(KnowledgeDocument)
        distance = KnowledgeChunk.embedding.cosine_distance(query_embedding)

        stmt = (
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.document_id,
                KnowledgeChunk.content,
                KnowledgeChunk.page_number,
                (1 - distance).label("score"),
                KnowledgeDocument.title,
                KnowledgeDocument.source_url,
                KnowledgeDocument.valid_until,
                superseding_document.title.label("superseded_by_title"),
            )
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .outerjoin(
                superseding_document,
                superseding_document.id == KnowledgeDocument.superseded_by_document_id,
            )
            .order_by(distance)
            .limit(top_k)
        )
        result = await self._session.execute(stmt)
        return [
            SimilarityMatch(
                chunk_id=row.id,
                document_id=row.document_id,
                content=row.content,
                page_number=row.page_number,
                score=float(row.score),
                title=row.title,
                source_url=row.source_url,
                valid_until=row.valid_until,
                superseded_by_title=row.superseded_by_title,
            )
            for row in result
        ]
