"""User-facing endpoints — `/api/session`, `/api/messages`, `/api/chat`, `/api/trending`
(docs/11-coding-standard.md §2).

Phase 8 (docs/IMPLEMENTATION_PLAN.md) wires session listing + message history only.
`POST /api/chat` lands in Phase 9; `GET /api/trending` lands in Phase 11.

Routers only parse/validate the request and delegate to `services/chat_service.py`
(docs/11-coding-standard.md §4) — no business logic here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import InvalidRequestError
from app.schemas.message import MessageItem, MessagesRequest, MessagesResponse
from app.schemas.session import SessionListItem, SessionListResponse
from app.services import chat_service

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
