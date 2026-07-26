"""Operator-only endpoints — `/api/opr/*` (docs/11-coding-standard.md §2).

Phase 7 (docs/IMPLEMENTATION_PLAN.md) wires knowledge ingestion/management only:
`POST /api/opr/ingest`, `GET /api/opr/knowledge`, `DELETE /api/opr/knowledge/{id}`
(docs/06-api-specification.md §6/§7/§7.1). `POST /api/opr/chat` lands in Phase 10.

Routers only parse/validate the request and delegate to `services/ingestion_service.py`
(docs/11-coding-standard.md §4) — no business logic here.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import InvalidRequestError
from app.middleware.rate_limit import rate_limit_dependency
from app.schemas.knowledge import (
    IngestResponse,
    KnowledgeDeleteResponse,
    KnowledgeListItem,
    KnowledgeListResponse,
)
from app.services import ingestion_service

router = APIRouter(prefix="/api/opr", tags=["operator"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _parse_valid_until(raw: str | None) -> date | None:
    if raw is None or raw == "":
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise InvalidRequestError(
            f"valid_until must be an ISO 8601 date (YYYY-MM-DD), got {raw!r}."
        ) from exc


def _parse_supersedes_document_id(raw: str | None) -> uuid.UUID | None:
    if raw is None or raw == "":
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise InvalidRequestError(
            f"supersedes_document_id must be a valid UUID, got {raw!r}."
        ) from exc


@router.post("/ingest", status_code=202)
async def ingest(
    background_tasks: BackgroundTasks,
    session: SessionDep,
    _rate_limit: Annotated[None, Depends(rate_limit_dependency("/api/opr/ingest"))],
    file: Annotated[UploadFile | None, File()] = None,
    text: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    valid_until: Annotated[str | None, Form()] = None,
    supersedes_document_id: Annotated[str | None, Form()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> IngestResponse:
    """`POST /api/opr/ingest` — docs/06-api-specification.md §6.

    Exactly one of `file`/`text` must be provided.
    """
    has_file = file is not None and bool(file.filename)
    has_text = bool(text)
    if has_file == has_text:
        raise InvalidRequestError("Exactly one of `file` or `text` must be provided.")

    raw_bytes: bytes | None = None
    if has_file:
        assert file is not None
        raw_bytes = await file.read()

    knowledge_id, status = await ingestion_service.ingest_document(
        session,
        background_tasks,
        source_type="file" if has_file else "text",
        raw_bytes=raw_bytes,
        text=text,
        title=title or (file.filename if has_file and file is not None else None),
        valid_until=_parse_valid_until(valid_until),
        supersedes_document_id=_parse_supersedes_document_id(supersedes_document_id),
        idempotency_key=idempotency_key,
    )
    return IngestResponse(knowledge_id=knowledge_id, status=status)  # type: ignore[arg-type]


@router.get("/knowledge")
async def list_knowledge(
    session: SessionDep,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> KnowledgeListResponse:
    """`GET /api/opr/knowledge` — docs/06-api-specification.md §7."""
    documents, total = await ingestion_service.list_knowledge(
        session, status=status, limit=limit, offset=offset
    )
    return KnowledgeListResponse(
        total=total,
        knowledge=[
            KnowledgeListItem(
                id=document.id,
                title=document.title,
                url=document.source_url,
                source_type=document.source_type,
                ingested_at=document.ingested_at,
                status=document.status,
                chunk_count=document.chunk_count,
                valid_until=document.valid_until,
                superseded_by_document_id=document.superseded_by_document_id,
            )
            for document in documents
        ],
    )


@router.delete("/knowledge/{knowledge_id}")
async def delete_knowledge(
    knowledge_id: uuid.UUID,
    session: SessionDep,
) -> KnowledgeDeleteResponse:
    """`DELETE /api/opr/knowledge/{knowledge_id}` — docs/06-api-specification.md §7.1."""
    chunks_removed = await ingestion_service.delete_knowledge(session, knowledge_id=knowledge_id)
    return KnowledgeDeleteResponse(
        knowledge_id=knowledge_id, status="deleted", chunks_removed=chunks_removed
    )
