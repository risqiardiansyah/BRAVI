"""Direct unit tests for `app.graphs.nodes.condense_history` —
docs/05-ai-agent-design.md §2.2/§2.3, docs/17-memory-strategy.md §4,
docs/IMPLEMENTATION_PLAN.md Phase 14.

`tests/integration/test_user_chat_graph.py`/`test_operator_chat_graph.py` run the full
chat graphs, but neither seeds enough messages to exceed
`CONTEXT_CONDENSATION_MAX_TURNS` (10), so this node's real condensation branch (past
the early "not enough turns yet" return) is otherwise never exercised. These tests
call `condense_history` directly against a session with 11+ persisted messages,
stubbing `bedrock_client` at this node module's own binding (same pattern as
`tests/integration/test_user_chat_graph.py`'s `stub_bedrock` fixture).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.clients.bedrock_client import PromptPayload
from app.config import settings
from app.db import normalize_asyncpg_url
from app.graphs.chat_state import ChatState
from app.graphs.nodes import condense_history as condense_history_module
from app.models.message import Message
from app.models.session import Session
from app.repositories.message_repository import MessageRepository
from app.repositories.session_repository import SessionRepository


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


class _StubBedrockClient:
    def __init__(self, tokens: list[str] | None = None) -> None:
        self.generate_calls: list[PromptPayload] = []
        self.tokens = tokens or ["Ringkasan ", "percakapan."]

    async def generate_stream(self, prompt: PromptPayload, **_params: Any) -> Any:
        self.generate_calls.append(prompt)
        for token in self.tokens:
            yield token


@pytest.fixture
def stub_bedrock(monkeypatch: pytest.MonkeyPatch) -> _StubBedrockClient:
    stub = _StubBedrockClient()
    monkeypatch.setattr(condense_history_module, "bedrock_client", stub)
    return stub


async def _make_session_with_messages(db_session: AsyncSession, *, count: int) -> Session:
    session_row = await SessionRepository(db_session).create(
        Session(user_id="test-user", persona="user")
    )
    await db_session.flush()
    repo = MessageRepository(db_session)
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        await repo.create(
            Message(session_id=session_row.session_id, role=role, content=f"turn {i}")
        )
    await db_session.flush()
    return session_row


def _config(db_session: AsyncSession) -> dict[str, Any]:
    return {"configurable": {"session": db_session}}


async def test_condense_history_below_threshold_returns_existing_summary_unchanged(
    db_session: AsyncSession, stub_bedrock: _StubBedrockClient
) -> None:
    """Sanity check for the already-covered early-return branch: no Bedrock call, no
    mutation, when the turn count doesn't exceed `CONTEXT_CONDENSATION_MAX_TURNS`."""
    session_row = await _make_session_with_messages(db_session, count=3)
    state: ChatState = {"session_id": session_row.session_id}

    result = await condense_history_module.condense_history(state, _config(db_session))

    assert result == {"history_summary": None}
    assert stub_bedrock.generate_calls == []


async def test_condense_history_over_threshold_generates_new_summary(
    db_session: AsyncSession, stub_bedrock: _StubBedrockClient
) -> None:
    assert settings.CONTEXT_CONDENSATION_MAX_TURNS == 10
    session_row = await _make_session_with_messages(db_session, count=12)
    state: ChatState = {"session_id": session_row.session_id}

    result = await condense_history_module.condense_history(state, _config(db_session))

    assert result["history_summary"] == "Ringkasan percakapan."
    assert result["text_model_used"] == settings.BEDROCK_TEXT_MODEL
    assert len(stub_bedrock.generate_calls) == 1

    refreshed = await SessionRepository(db_session).get_by_id(session_row.session_id)
    assert refreshed is not None
    assert refreshed.history_summary == "Ringkasan percakapan."
    assert refreshed.history_summary_updated_at is not None


async def test_condense_history_folds_existing_summary_into_prompt(
    db_session: AsyncSession, stub_bedrock: _StubBedrockClient
) -> None:
    """When a `history_summary` already exists, it is prepended as a synthetic first
    "turn" in the same condensation prompt (module docstring) rather than a second
    template — assert the previous summary text actually reaches the prompt payload."""
    session_row = await _make_session_with_messages(db_session, count=12)
    session_row.history_summary = "Ringkasan sebelumnya tentang klaim asuransi."
    await db_session.flush()

    state: ChatState = {"session_id": session_row.session_id}
    await condense_history_module.condense_history(state, _config(db_session))

    assert len(stub_bedrock.generate_calls) == 1
    prompt = stub_bedrock.generate_calls[0]
    prompt_text = prompt.messages[0].content[0].text
    assert "Ringkasan sebelumnya tentang klaim asuransi." in prompt_text


async def test_condense_history_no_new_messages_since_last_summary_skips_bedrock(
    db_session: AsyncSession, stub_bedrock: _StubBedrockClient
) -> None:
    """If every message was already folded into `history_summary` (nothing created
    after `history_summary_updated_at`), the node must not call Bedrock again and must
    return the existing summary unchanged."""
    from datetime import UTC, datetime, timedelta

    session_row = await _make_session_with_messages(db_session, count=12)
    session_row.history_summary = "Ringkasan yang sudah lengkap."
    # Set updated_at safely in the future relative to every message's created_at.
    session_row.history_summary_updated_at = datetime.now(UTC) + timedelta(days=1)
    await db_session.flush()

    state: ChatState = {"session_id": session_row.session_id}
    result = await condense_history_module.condense_history(state, _config(db_session))

    assert result == {"history_summary": "Ringkasan yang sudah lengkap."}
    assert stub_bedrock.generate_calls == []
