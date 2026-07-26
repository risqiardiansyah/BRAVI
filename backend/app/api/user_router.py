"""User-facing endpoints — `/api/session`, `/api/messages`, `/api/chat`, `/api/trending`
(docs/11-coding-standard.md §2).

Phase 8 (docs/IMPLEMENTATION_PLAN.md) wired session listing + message history.
`POST /api/chat` is this phase's (9) own addition; `GET /api/trending` lands in Phase 11.

Routers only parse/validate the request and delegate to `services/chat_service.py`
(docs/11-coding-standard.md §4) — no business logic here.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.config import settings
from app.db import get_session
from app.errors import FileTooLargeError, InvalidRequestError, UnsupportedMediaTypeError
from app.middleware.rate_limit import rate_limit_dependency
from app.schemas.chat import ChatRequestFields
from app.schemas.message import MessageItem, MessagesRequest, MessagesResponse
from app.schemas.session import SessionListItem, SessionListResponse
from app.services import chat_service

router = APIRouter(prefix="/api", tags=["user"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# docs/08-security.md §3 — chat image upload MIME allowlist, mapped to the
# `image_format` literal `clients/bedrock_client.py`'s multimodal payload expects.
_ALLOWED_IMAGE_MIME_TYPES = {"image/png": "png", "image/jpeg": "jpeg", "image/webp": "webp"}


@router.get("/session")
async def list_sessions(
    session: SessionDep,
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> SessionListResponse:
    """`GET /api/session` — docs/06-api-specification.md §1."""
    if not user_id.strip():
        raise InvalidRequestError("Missing required query parameter `user_id`.")

    rows, total = await chat_service.list_sessions_for_user(
        session, user_id=user_id, limit=limit, offset=offset
    )
    return SessionListResponse(
        user_id=user_id,
        total=total,
        sessions=[
            SessionListItem(
                session_id=row.session_id,
                persona=row.persona,
                title=row.title,
                created_at=row.created_at,
            )
            for row in rows
        ],
    )


@router.post("/messages")
async def get_messages(payload: MessagesRequest, session: SessionDep) -> MessagesResponse:
    """`POST /api/messages` — docs/06-api-specification.md §3."""
    rows = await chat_service.get_session_messages(session, session_id=payload.session_id)
    return MessagesResponse(
        session_id=payload.session_id,
        messages=[
            MessageItem(role=row.role, content=row.content, created_at=row.created_at)
            for row in rows
        ],
    )


async def _parse_chat_request(request: Request) -> tuple[ChatRequestFields, UploadFile | None]:
    """`POST /api/chat` accepts `multipart/form-data` (when `file` is attached) or plain
    JSON otherwise (docs/06-api-specification.md §2) — genuinely dual wire formats on one
    route isn't expressible via FastAPI's declarative `Body`/`Form` params (which commit to
    one shape at decoration time), so the raw `Request` is parsed manually here and
    validated against the same `ChatRequestFields` model either way.
    """
    content_type = request.headers.get("content-type", "")
    raw: dict[str, Any]
    file: UploadFile | None = None

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        raw = {
            "session_id": form.get("session_id") or None,
            "question": form.get("question"),
            "user_id": form.get("user_id"),
        }
        maybe_file = form.get("file")
        if isinstance(maybe_file, UploadFile) and maybe_file.filename:
            file = maybe_file
    else:
        try:
            raw = await request.json()
        except ValueError as exc:
            raise InvalidRequestError("Request body must be valid JSON.") from exc
        if not isinstance(raw, dict):
            raise InvalidRequestError("Request body must be a JSON object.")

    try:
        fields = ChatRequestFields.model_validate(raw)
    except ValidationError as exc:
        raise InvalidRequestError(str(exc)) from exc

    if not fields.question.strip():
        raise InvalidRequestError("Missing required field `question`.")
    if not fields.user_id.strip():
        raise InvalidRequestError("Missing required field `user_id`.")

    return fields, file


async def _read_and_validate_image(file: UploadFile | None) -> tuple[bytes | None, str | None]:
    """MIME allowlist + size-limit checks (docs/08-security.md §3) for `/api/chat`'s
    optional image upload — 415/413 per docs/06-api-specification.md §2's error table.
    Real malware/content scanning (docs/08-security.md §8a) is Phase 12 scope.
    """
    if file is None:
        return None, None

    content_type = file.content_type or ""
    image_format = _ALLOWED_IMAGE_MIME_TYPES.get(content_type)
    if image_format is None:
        raise UnsupportedMediaTypeError(f"Unsupported file type: {content_type!r}.")

    raw_bytes = await file.read()
    max_bytes = settings.MAX_IMAGE_UPLOAD_MB * 1024 * 1024
    if len(raw_bytes) > max_bytes:
        raise FileTooLargeError(f"File exceeds the {settings.MAX_IMAGE_UPLOAD_MB}MB limit.")

    return raw_bytes, image_format


@router.post("/chat")
async def chat(
    request: Request,
    session: SessionDep,
    _rate_limit: Annotated[None, Depends(rate_limit_dependency("/api/chat"))],
) -> StreamingResponse:
    """`POST /api/chat` — docs/06-api-specification.md §0/§2.

    Session resolution/validation/image checks all happen here, before the SSE stream
    opens, so a `400`/`404`/`413`/`415` is a normal JSON error response (docs/06-api-
    specification.md §2: "404 on a bad session_id is returned before the stream opens").
    Everything after that point (the graph run itself) can only fail as an in-stream SSE
    `error` event, handled inside `chat_service.stream_user_chat_response`.
    """
    fields, file = await _parse_chat_request(request)
    image_bytes, image_format = await _read_and_validate_image(file)

    resolved = await chat_service.resolve_session(
        session, session_id=fields.session_id, user_id=fields.user_id, persona="user"
    )
    await chat_service.persist_message(
        session,
        session_id=resolved.session_id,
        role="user",
        content=fields.question,
        has_image=image_bytes is not None,
    )
    await session.commit()

    generator = chat_service.stream_user_chat_response(
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
