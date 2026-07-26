"""Repository for `knowledge_documents` — docs/07-database-design.md §3.4."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.models.knowledge_document import KnowledgeDocument
from app.repositories.base import BaseRepository


class KnowledgeDocumentRepository(BaseRepository[KnowledgeDocument]):
    model = KnowledgeDocument

    async def mark_completed(self, document: KnowledgeDocument, *, chunk_count: int) -> None:
        document.status = "completed"
        document.chunk_count = chunk_count
        document.ingested_at = datetime.now(UTC)
        await self._session.flush()

    async def mark_failed(self, document: KnowledgeDocument, *, error_message: str) -> None:
        document.status = "failed"
        document.error_message = error_message
        await self._session.flush()

    async def get_by_idempotency_key(self, idempotency_key: str) -> KnowledgeDocument | None:
        """Looks up a prior `/api/opr/ingest` request by its `Idempotency-Key`
        (docs/06-api-specification.md §6, docs/22-error-handling.md §4)."""
        result = await self._session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none()

    async def set_superseded_by(
        self, document: KnowledgeDocument, *, new_document_id: uuid.UUID
    ) -> None:
        """Sets `superseded_by_document_id` on the OLD document being superseded,
        pointing at the newly created document — docs/07-database-design.md §5b
        ("on the old document being superseded, not on the new one")."""
        document.superseded_by_document_id = new_document_id
        await self._session.flush()

    async def list_paginated(
        self, *, status: str | None, limit: int, offset: int
    ) -> tuple[list[KnowledgeDocument], int]:
        """Newest-first page of `knowledge_documents`, plus the total matching count
        (docs/06-api-specification.md §7)."""
        filters = [KnowledgeDocument.status == status] if status else []

        count_result = await self._session.execute(
            select(func.count()).select_from(KnowledgeDocument).where(*filters)
        )
        total = count_result.scalar_one()

        rows_result = await self._session.execute(
            select(KnowledgeDocument)
            .where(*filters)
            .order_by(KnowledgeDocument.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows_result.scalars().all()), total
