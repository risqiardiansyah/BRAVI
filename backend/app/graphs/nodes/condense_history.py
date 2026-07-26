"""`condense_history` node — docs/05-ai-agent-design.md §2.2/§2.3, docs/17-memory-strategy.md §4.

Only invoked (in the sense of doing real work) when the session's raw `messages` row count
exceeds `CONTEXT_CONDENSATION_MAX_TURNS` (both roles count — §4's "not user-turns only").
Incremental: re-summarizes only messages added since `sessions.history_summary_updated_at`,
folding them into the existing `history_summary` via a single small text-model call — the
existing summary (when present) is prepended as a synthetic first "turn" so the same
canonical condensation prompt (docs/prompts/ai-agent.md §7) does the folding in one call,
without inventing a second template.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.bedrock_client import (
    PromptContentBlock,
    PromptMessage,
    PromptPayload,
    bedrock_client,
)
from app.config import settings
from app.graphs.chat_state import ChatState
from app.graphs.prompts import build_condensation_prompt
from app.repositories.message_repository import MessageRepository
from app.repositories.session_repository import SessionRepository


async def condense_history(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    session: AsyncSession = config["configurable"]["session"]
    session_id = state["session_id"]

    session_row = await SessionRepository(session).get_by_id(session_id)
    assert session_row is not None, f"session {session_id} vanished mid-request"

    messages = await MessageRepository(session).list_by_session_id(session_id)
    if len(messages) <= settings.CONTEXT_CONDENSATION_MAX_TURNS:
        return {"history_summary": session_row.history_summary}

    since = session_row.history_summary_updated_at
    new_messages = [m for m in messages if since is None or m.created_at > since]
    if not new_messages:
        return {"history_summary": session_row.history_summary}

    turns_text = "\n".join(f"{m.role}: {m.content}" for m in new_messages)
    if session_row.history_summary:
        previous_summary = session_row.history_summary
        turns_text = f"[Ringkasan percakapan sebelumnya]: {previous_summary}\n\n{turns_text}"

    prompt = PromptPayload(
        messages=[
            PromptMessage(
                role="user",
                content=[PromptContentBlock(text=build_condensation_prompt(turns_text))],
            )
        ]
    )

    chunks: list[str] = []
    async for token in bedrock_client.generate_stream(prompt):
        chunks.append(token)
    new_summary = "".join(chunks).strip()

    session_row.history_summary = new_summary
    session_row.history_summary_updated_at = datetime.now(UTC)
    await session.flush()

    return {"history_summary": new_summary, "text_model_used": settings.BEDROCK_TEXT_MODEL}
