"""Session resolution + message persistence — docs/06-api-specification.md §1-§3/§5,
docs/07-database-design.md §3.1/§3.2, docs/02-functional-requirements.md FR-1/FR-3.

`resolve_session`/`persist_message` are shared by `/api/chat` and `/api/opr/chat`
(Phase 9/10's chat graphs will call them from their own `persist_message` node) as well
as this phase's own `/api/session`/`/api/messages` endpoints — built now per
`11-coding-standard.md` §4 ("Services contain orchestration/business logic") so later
phases reuse this instead of duplicating it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.bedrock_client import BedrockInvocationError, BedrockUnavailableError
from app.config import settings
from app.errors import SessionNotFoundError
from app.graphs.chat_state import ChatState
from app.models.message import Message
from app.models.session import Session
from app.repositories.message_repository import MessageRepository
from app.repositories.session_repository import SessionRepository
from app.schemas.chat import ChatSourceItem, ChatStreamEvent
from app.utils.sse import KEEPALIVE_COMMENT, format_sse_event, stream_with_keepalive

logger = logging.getLogger(__name__)

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


def _map_exception_to_error_event(exc: Exception) -> tuple[str, str]:
    """Maps an exception raised mid-stream to the `code`/`message` of an SSE `error`
    event — docs/22-error-handling.md §2. Never leaks a raw internal message in
    production (docs/11-coding-standard.md §6), mirroring `errors.internal_error_handler`.
    """
    if isinstance(exc, BedrockUnavailableError):
        return "BEDROCK_UNAVAILABLE", str(exc)
    if isinstance(exc, BedrockInvocationError):
        # Only reached after `BEDROCK_MAX_RETRIES` is exhausted (non-retryable errors are
        # non-Bedrock-outage validation failures, folded into INTERNAL_ERROR below) — the
        # registry's only code for "Bedrock retries exhausted" is BEDROCK_TIMEOUT
        # regardless of the underlying transient cause (timeout/throttling/internal).
        if exc.retryable:
            code = "BEDROCK_TIMEOUT"
        else:
            code = "INTERNAL_ERROR"
        message = str(exc) if settings.APP_ENV != "production" else "Internal server error."
        return code, message

    message = str(exc) if settings.APP_ENV != "production" else "Internal server error."
    return "INTERNAL_ERROR", message


def _build_done_event(session_id: uuid.UUID, state: ChatState) -> ChatStreamEvent:
    """Builds the terminal `done` SSE event from the graph's final state —
    docs/06-api-specification.md §0. `sources` is `null` whenever short-circuited (no
    retrieval ran), an array otherwise."""
    short_circuited = bool(state.get("short_circuited"))
    sources: list[ChatSourceItem] | None = None
    if not short_circuited:
        sources = [ChatSourceItem(**item) for item in state.get("sources") or []]

    return ChatStreamEvent(
        type="done",
        session_id=session_id,
        answer=state.get("answer"),
        sources=sources,
        short_circuited=short_circuited,
        short_circuit_reason=state.get("short_circuit_reason"),
        mode=state.get("mode"),
    )


async def stream_user_chat_response(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: str,
    original_question: str,
    question: str,
    image_bytes: bytes | None,
    image_format: str | None,
) -> AsyncIterator[str]:
    """Runs `user_chat_graph` for one turn and yields SSE `data:`/keepalive lines —
    docs/06-api-specification.md §0/§2, docs/11-coding-standard.md §7 ("never buffer the
    full answer server-side... graph's async streaming invocation").

    The caller (docs/api/user_router.py) has already resolved the session and persisted
    the user's own message before calling this — everything here only ever produces the
    assistant's side of the turn. Exactly one terminal event (`done` or `error`) is
    yielded; any exception raised while iterating the graph is caught and converted to a
    well-formed `error` event rather than aborting the connection (docs/22-error-handling.md
    §6), since HTTP headers/status are already committed once streaming has started.
    """
    # Deferred import: `graphs/nodes/persist_message.py` imports this module to reuse
    # `persist_message` (docs comment above `persist_message`'s own definition), and
    # `user_chat_graph` wires that node — a module-level import here would be circular.
    from app.graphs.user_chat_graph import user_chat_graph

    initial_state: ChatState = {
        "session_id": session_id,
        "user_id": user_id,
        "persona": "user",
        "question": question,
        "original_question": original_question,
        "image_bytes": image_bytes,
        "image_format": image_format,  # type: ignore[typeddict-item]
        "short_circuited": False,
        "short_circuit_reason": None,
        "mode": None,
    }

    try:
        final_state: ChatState | None = None
        source = user_chat_graph.astream(
            initial_state,
            config={"configurable": {"session": session}},
            stream_mode=["custom", "values"],
        )
        async for item in stream_with_keepalive(
            source, interval_seconds=settings.SSE_KEEPALIVE_INTERVAL_SECONDS
        ):
            if item is None:
                yield KEEPALIVE_COMMENT
                continue
            # `astream(..., stream_mode=[...])` yields `(mode, chunk)` 2-tuples at
            # runtime (docs/11-coding-standard.md §7); the installed langgraph stub
            # types this loosely as `dict[str, Any] | Any`, which mypy would otherwise
            # (incorrectly) read as "unpack a dict's keys" rather than "unpack a tuple".
            mode, chunk = cast(tuple[str, Any], item)
            if mode == "custom":
                yield format_sse_event(
                    ChatStreamEvent(type="token", session_id=session_id, content=chunk["content"])
                )
            elif mode == "values":
                final_state = chunk

        assert final_state is not None, "graph produced no final state"
        if final_state.get("short_circuited") and final_state.get("answer"):
            yield format_sse_event(
                ChatStreamEvent(type="token", session_id=session_id, content=final_state["answer"])
            )

        await session.commit()
        yield format_sse_event(_build_done_event(session_id, final_state))
    except Exception as exc:  # noqa: BLE001 - always surfaced as a well-formed SSE error event
        await session.rollback()
        logger.exception("chat stream failed for session_id=%s", session_id)
        code, message = _map_exception_to_error_event(exc)
        yield format_sse_event(
            ChatStreamEvent(type="error", session_id=session_id, code=code, message=message)
        )
