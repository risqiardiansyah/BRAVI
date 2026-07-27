"""Operator-only endpoints — `/api/opr/*` (docs/11-coding-standard.md §2).

Phase 7 (docs/IMPLEMENTATION_PLAN.md) wired knowledge ingestion/management:
`POST /api/opr/ingest`, `GET /api/opr/knowledge`, `DELETE /api/opr/knowledge/{id}`
(docs/06-api-specification.md §6/§7/§7.1). Phase 10 added `POST /api/opr/chat`
(docs/06-api-specification.md §5). Phase 11 (this) adds `GET /api/opr/analytics`
(docs/06-api-specification.md §8).

Routers only parse/validate the request and delegate to `services/ingestion_service.py`/
`services/chat_service.py`/`services/analytics_service.py` (docs/11-coding-standard.md
§4) — no business logic here.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.malware_scanner import scan_bytes
from app.config import settings
from app.db import get_session
from app.errors import (
    FileTooLargeError,
    InvalidRequestError,
    MalwareDetectedError,
    UnsupportedMediaTypeError,
)
from app.middleware.rate_limit import rate_limit_dependency
from app.schemas.analytics import AnalyticsResponse
from app.schemas.knowledge import (
    IngestResponse,
    KnowledgeDeleteResponse,
    KnowledgeListItem,
    KnowledgeListResponse,
)
from app.services import analytics_service, chat_service, ingestion_service
from app.utils.chat_request import parse_chat_request, read_and_validate_image

router = APIRouter(prefix="/api/opr", tags=["operator"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# docs/08-security.md §3 — Input Validation Rules for `/api/opr/ingest`.
_INGEST_TEXT_MAX_LENGTH = 200_000


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

    if has_text:
        assert text is not None
        if len(text) > _INGEST_TEXT_MAX_LENGTH:
            raise InvalidRequestError(
                f"`text` must not exceed {_INGEST_TEXT_MAX_LENGTH} characters "
                f"(docs/08-security.md §3), got {len(text)}."
            )

    raw_bytes: bytes | None = None
    if has_file:
        assert file is not None
        if file.content_type != "application/pdf":
            raise UnsupportedMediaTypeError(
                f"Unsupported file type: {file.content_type!r}. Only `application/pdf` "
                "is accepted (docs/08-security.md §3)."
            )
        raw_bytes = await file.read()
        max_bytes = settings.MAX_FILE_UPLOAD_MB * 1024 * 1024
        if len(raw_bytes) > max_bytes:
            raise FileTooLargeError(f"File exceeds the {settings.MAX_FILE_UPLOAD_MB}MB limit.")
        if not scan_bytes(raw_bytes):
            raise MalwareDetectedError("Uploaded file failed content scanning.")

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


@router.post("/chat")
async def operator_chat(
    request: Request,
    session: SessionDep,
    _rate_limit: Annotated[None, Depends(rate_limit_dependency("/api/opr/chat"))],
) -> StreamingResponse:
    """`POST /api/opr/chat` — docs/06-api-specification.md §0/§5.

    Same request-parsing/session-resolution/image-validation shape as `POST /api/chat`
    (`app/api/user_router.py`, Phase 9) — shared via `app/utils/chat_request.py` — but
    runs `operator_chat_graph` (`persona="operator"`) instead, which additionally wires
    `classify_add_knowledge_intent`/`route_by_intent`/`generate_summary`
    (docs/05-ai-agent-design.md §2.2).
    """
    fields, file = await parse_chat_request(request)
    image_bytes, image_format = await read_and_validate_image(file)

    resolved = await chat_service.resolve_session(
        session, session_id=fields.session_id, user_id=fields.user_id, persona="operator"
    )
    await chat_service.persist_message(
        session,
        session_id=resolved.session_id,
        role="user",
        content=fields.question,
        has_image=image_bytes is not None,
    )
    await session.commit()

    generator = chat_service.stream_operator_chat_response(
        session,
        session_id=resolved.session_id,
        user_id=fields.user_id,
        original_question=fields.question,
        question=fields.question,
        image_bytes=image_bytes,
        image_format=image_format,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )


@router.get("/analytics")
async def analytics(
    session: SessionDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> AnalyticsResponse:
    """`GET /api/opr/analytics` — docs/06-api-specification.md §8.

    Both `from`/`to` are optional; omitted defaults to a rolling
    `ANALYTICS_DEFAULT_WINDOW_DAYS`-day window ending today (neither
    `06-api-specification.md` §8 nor `02-functional-requirements.md` FR-9 defines a
    default range).
    """
    if date_from is not None and date_to is not None and date_from > date_to:
        raise InvalidRequestError("`from` must not be after `to`.")

    return await analytics_service.get_operator_analytics(
        session, date_from=date_from, date_to=date_to
    )
