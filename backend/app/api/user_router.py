"""User-facing endpoints — `/api/session`, `/api/messages`, `/api/chat`, `/api/trending`
(docs/11-coding-standard.md §2).

Phase 8 (docs/IMPLEMENTATION_PLAN.md) wired session listing + message history.
Phase 9 added `POST /api/chat`; Phase 11 (this) adds `GET /api/trending`.

Routers only parse/validate the request and delegate to `services/chat_service.py`/
`services/analytics_service.py` (docs/11-coding-standard.md §4) — no business logic here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import InvalidRequestError
from app.middleware.rate_limit import rate_limit_dependency
from app.schemas.analytics import TrendingResponse
from app.schemas.message import MessageItem, MessagesRequest, MessagesResponse
from app.schemas.session import SessionListItem, SessionListResponse
from app.services import analytics_service, chat_service
from app.services.analytics_service import TRENDING_DEFAULT_LIMIT, TRENDING_DEFAULT_WINDOW_DAYS
from app.utils.chat_request import parse_chat_request, read_and_validate_image

router = APIRouter(prefix="/api", tags=["user"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


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
    fields, file = await parse_chat_request(request)
    image_bytes, image_format = await read_and_validate_image(file)

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


@router.get("/trending")
async def trending(
    session: SessionDep,
    limit: int = TRENDING_DEFAULT_LIMIT,
    window_days: int = TRENDING_DEFAULT_WINDOW_DAYS,
) -> TrendingResponse:
    """`GET /api/trending` — docs/06-api-specification.md §4."""
    if limit < 1:
        raise InvalidRequestError("`limit` must be a positive integer.")
    if window_days < 1:
        raise InvalidRequestError("`window_days` must be a positive integer.")

    resolved_window_days, items = await analytics_service.get_trending(
        session, window_days=window_days, limit=limit
    )
    return TrendingResponse(window_days=resolved_window_days, trending=items)
