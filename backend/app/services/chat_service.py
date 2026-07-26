"""Session resolution + message persistence — docs/06-api-specification.md §1-§3/§5,
docs/07-database-design.md §3.1/§3.2, docs/02-functional-requirements.md FR-1/FR-3.

`resolve_session`/`persist_message` are shared by `/api/chat` and `/api/opr/chat`
(Phase 9/10's chat graphs will call them from their own `persist_message` node) as well
as this phase's own `/api/session`/`/api/messages` endpoints — built now per
`11-coding-standard.md` §4 ("Services contain orchestration/business logic") so later
phases reuse this instead of duplicating it.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import SessionNotFoundError
from app.models.message import Message
from app.models.session import Session
from app.repositories.message_repository import MessageRepository
from app.repositories.session_repository import SessionRepository

TITLE_MAX_LENGTH = 60


async def resolve_session(
    session: AsyncSession, *, session_id: uuid.UUID | None, user_id: str, persona: str
) -> Session:
    """Session resolution rule shared by `/api/chat`/`/api/opr/chat`
    (docs/06-api-specification.md §2/§5):
    - `session_id` omitted/empty -> auto-create a new session for this `persona`/`user_id`.
    - `session_id` provided and exists -> reuse it as-is (no persona/user_id check —
      neither doc defines a mismatch case).
    - `session_id` provided but unknown -> `SessionNotFoundError` (no silent auto-create).
    """
    repo = SessionRepository(session)
    if session_id is None:
        return await repo.create(Session(user_id=user_id, persona=persona))

    existing = await repo.get_by_id(session_id)
    if existing is None:
        raise SessionNotFoundError(f"Session {session_id} was not found.")
    return existing


async def persist_message(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    role: str,
    content: str,
    has_image: bool = False,
) -> Message:
    """Persists one message; the first `role='user'` message for a session sets
    `sessions.title` once — plain truncation to `TITLE_MAX_LENGTH` chars, no LLM call,
    never overwritten afterward (docs/07-database-design.md §3.1)."""
    message = await MessageRepository(session).create(
        Message(session_id=session_id, role=role, content=content, has_image=has_image)
    )

    if role == "user":
        session_repo = SessionRepository(session)
        session_row = await session_repo.get_by_id(session_id)
        if session_row is not None and session_row.title is None:
            session_row.title = content.strip()[:TITLE_MAX_LENGTH]
            await session.flush()

    return message


async def list_sessions_for_user(
    session: AsyncSession, *, user_id: str, limit: int, offset: int
) -> tuple[list[Session], int]:
    """`GET /api/session` — docs/06-api-specification.md §1."""
    return await SessionRepository(session).list_by_user_id(user_id, limit=limit, offset=offset)


async def get_session_messages(session: AsyncSession, *, session_id: uuid.UUID) -> list[Message]:
    """`POST /api/messages` — docs/06-api-specification.md §3. Raises
    `SessionNotFoundError` for an unknown `session_id`."""
    existing = await SessionRepository(session).get_by_id(session_id)
    if existing is None:
        raise SessionNotFoundError(f"Session {session_id} was not found.")
    return await MessageRepository(session).list_by_session_id(session_id)
