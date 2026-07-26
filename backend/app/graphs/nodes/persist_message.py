"""`persist_message` node — docs/05-ai-agent-design.md §2.2/§2.3.

Persists the assistant's final answer. Delegates to `services/chat_service.persist_message`
(built in Phase 8) rather than reimplementing the `sessions.title` set-once rule a second
time — that function already handles both `role='user'`/`role='assistant'` rows generically;
this node always calls it with `role="assistant"` since the user's own question is persisted
by the API/service layer *before* the graph runs (docs/06-api-specification.md §2's session
resolution happens pre-stream; the graph itself only ever produces the assistant's reply).
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.graphs.chat_state import ChatState
from app.services import chat_service


async def persist_message(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    session: AsyncSession = config["configurable"]["session"]
    await chat_service.persist_message(
        session,
        session_id=state["session_id"],
        role="assistant",
        content=state.get("answer") or "",
        has_image=False,
    )
    return {}
