"""Orchestration for `/api/opr/ingest`, `/api/opr/knowledge`, `DELETE .../{id}` —
docs/06-api-specification.md §6/§7/§7.1, docs/07-database-design.md §5a/§5b,
docs/02-functional-requirements.md FR-7/FR-8/FR-13.

Routers only parse/validate the request and call these functions
(docs/11-coding-standard.md §4) — business logic (idempotency-key handling,
supersession wiring, cascade-delete side effects) lives here.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal
from app.errors import IdempotencyKeyConflictError, InvalidRequestError, KnowledgeNotFoundError
from app.graphs.ingestion_graph import ingestion_graph
from app.graphs.ingestion_state import IngestionState
from app.models.ingestion_job import IngestionJob
from app.models.knowledge_document import KnowledgeDocument
from app.repositories.ingestion_job_repository import IngestionJobRepository
from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from app.repositories.knowledge_source_repository import KnowledgeSourceRepository

logger = logging.getLogger(__name__)


async def ingest_document(
    session: AsyncSession,
    background_tasks: BackgroundTasks,
    *,
    source_type: Literal["file", "text"],
    raw_bytes: bytes | None,
    text: str | None,
    title: str | None,
    valid_until: date | None,
    supersedes_document_id: uuid.UUID | None,
    idempotency_key: str | None,
) -> tuple[uuid.UUID, str]:
    """Creates the `knowledge_documents`/`ingestion_jobs` rows synchronously (so the
    caller can return `knowledge_id` immediately, docs/06-api-specification.md §6),
    then schedules the actual extract/chunk/embed/store pipeline as a background task
    (FR-7: "recommended to run as a background task/job if file is large").

    Returns `(knowledge_id, status)` — on an `Idempotency-Key` retry with matching
    content, `status` reflects the *original* request's current status rather than
    always being `"queued"` (docs/06-api-specification.md §6).
    """
    content_bytes = raw_bytes if source_type == "file" else (text or "").encode("utf-8")
    content_hash = hashlib.sha256(content_bytes or b"").hexdigest()

    document_repo = KnowledgeDocumentRepository(session)

    if idempotency_key:
        existing = await document_repo.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            if existing.content_hash == content_hash:
                # Same key, same content: a client retry after a timeout/dropped
                # response — return the original result, start no duplicate job.
                return existing.id, existing.status
            raise IdempotencyKeyConflictError(
                f"Idempotency-Key {idempotency_key!r} was already used with different "
                "file/text content."
            )

    superseded_document: KnowledgeDocument | None = None
    if supersedes_document_id is not None:
        superseded_document = await document_repo.get_by_id(supersedes_document_id)
        if superseded_document is None:
            raise InvalidRequestError(
                f"supersedes_document_id {supersedes_document_id} does not reference an "
                "existing knowledge document."
            )

    document = await document_repo.create(
        KnowledgeDocument(
            title=title,
            source_url=None,
            source_type=source_type,
            status="queued",
            valid_until=valid_until,
            idempotency_key=idempotency_key,
            content_hash=content_hash,
        )
    )

    if superseded_document is not None:
        # Set on the OLD document, pointing at the new one — docs/07-database-design.md §5b.
        await document_repo.set_superseded_by(superseded_document, new_document_id=document.id)

    job = await IngestionJobRepository(session).create(
        IngestionJob(job_type="on_demand", document_id=document.id, status="queued")
    )
    await session.commit()

    background_tasks.add_task(
        _run_on_demand_ingestion,
        document_id=document.id,
        job_id=job.id,
        source_type=source_type,
        source_ref=text if source_type == "text" else (title or "opr-ingest-file"),
        raw_bytes=raw_bytes,
    )

    return document.id, document.status


async def _run_on_demand_ingestion(
    *,
    document_id: uuid.UUID,
    job_id: uuid.UUID,
    source_type: Literal["file", "text"],
    source_ref: str | None,
    raw_bytes: bytes | None,
) -> None:
    """Background-task body — opens its own session, since the request-scoped one
    used by `ingest_document` is already closed by the time this runs (matches
    `app/jobs/run_initial_ingestion.py`'s one-session-per-concurrent-document pattern).
    """
    async with AsyncSessionLocal() as session:
        job_repo = IngestionJobRepository(session)
        job = await job_repo.get_by_id(job_id)
        if job is None:
            logger.error("on-demand ingestion: job_id=%s not found", job_id)
            return
        job.status = "processing"
        job.started_at = datetime.now(UTC)
        await session.flush()

        initial_state: IngestionState = {
            "source_type": source_type,
            "source_ref": source_ref or "",
            "document_id": document_id,
            "job_id": job_id,
            "knowledge_source_id": None,
            "content_hash": None,
            "raw_bytes": raw_bytes,
        }
        try:
            await ingestion_graph.ainvoke(
                initial_state, config={"configurable": {"session": session}}
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.exception("on-demand ingestion crashed for document_id=%s", document_id)
            # Best-effort: make sure a genuine bug (not a modeled pipeline failure,
            # which `update_ingestion_status` already persists) doesn't leave the
            # document stuck in `queued`/`processing` forever.
            async with AsyncSessionLocal() as failure_session:
                doc = await KnowledgeDocumentRepository(failure_session).get_by_id(document_id)
                failed_job = await IngestionJobRepository(failure_session).get_by_id(job_id)
                if doc is not None:
                    await KnowledgeDocumentRepository(failure_session).mark_failed(
                        doc, error_message=str(exc)
                    )
                if failed_job is not None:
                    await IngestionJobRepository(failure_session).mark_failed(
                        failed_job, error_message=str(exc)
                    )
                await failure_session.commit()


async def list_knowledge(
    session: AsyncSession, *, status: str | None, limit: int, offset: int
) -> tuple[list[KnowledgeDocument], int]:
    """`GET /api/opr/knowledge` — docs/06-api-specification.md §7."""
    return await KnowledgeDocumentRepository(session).list_paginated(
        status=status, limit=limit, offset=offset
    )


async def delete_knowledge(session: AsyncSession, *, knowledge_id: uuid.UUID) -> int:
    """`DELETE /api/opr/knowledge/{id}` — docs/06-api-specification.md §7.1,
    docs/07-database-design.md §5a. Returns `chunks_removed`.

    `note`: The endpoint carries no `user_id` (docs/06-api-specification.md §7.1 defines
    no request body/params), so the `user_id` field docs/07-database-design.md §5a's
    logging line calls for cannot actually be populated here — logged as `None`.
    """
    document_repo = KnowledgeDocumentRepository(session)
    document = await document_repo.get_by_id(knowledge_id)
    if document is None:
        raise KnowledgeNotFoundError(f"Knowledge document {knowledge_id} was not found.")

    title = document.title
    source_id = document.source_id
    chunks_removed = await KnowledgeChunkRepository(session).count_by_document_id(knowledge_id)

    # DB-level ON DELETE CASCADE removes knowledge_chunks; ingestion_jobs.document_id
    # -> NULL; superseded_by_document_id (on any other doc pointing at this one) ->
    # NULL — all enforced by the FK constraints themselves (docs/07-database-design.md §5a).
    await document_repo.delete(document)

    if source_id is not None:
        source_repo = KnowledgeSourceRepository(session)
        source = await source_repo.get_by_id(source_id)
        if source is not None:
            await source_repo.reset_ingested(source)

    await session.commit()
    logger.warning(
        "knowledge_document_deleted",
        extra={
            "user_id": None,
            "knowledge_id": str(knowledge_id),
            "title": title,
            "chunks_removed": chunks_removed,
        },
    )
    return chunks_removed
