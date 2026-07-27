"""In-flight SSE drain wired into the real chat-streaming path —
docs/10-deployment.md §4.1, docs/IMPLEMENTATION_PLAN.md Phase 13 task 2's own
Verification item: "start a slow mocked generation, send SIGTERM, confirm the client
still receives a complete `done` event." Mirrors `tests/integration/test_user_chat_graph.py`'s
fixture pattern (own throwaway `NullPool` engine, Bedrock stubbed at each node module).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.clients.bedrock_client import PromptPayload
from app.config import settings
from app.db import normalize_asyncpg_url
from app.graphs.nodes import condense_history as condense_history_module
from app.graphs.nodes import embed_question as embed_question_module
from app.graphs.nodes import generate_answer as generate_answer_module
from app.graphs.nodes import preprocess_input as preprocess_input_module
from app.models.session import Session
from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository, SimilarityMatch
from app.repositories.session_repository import SessionRepository
from app.services import chat_service
from app.shutdown import ShutdownState, track_stream


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


class _SlowStubBedrockClient:
    """Simulates a still-in-progress generation until `release` is set, so the test can
    observe the stream as genuinely "in flight" before letting it complete."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        return [[0.01] * 1024 for _ in texts]

    async def generate_stream(self, prompt: PromptPayload, **_params: Any) -> AsyncIterator[str]:
        yield "Sebagian "
        await self.release.wait()
        yield "jawaban selesai."


@pytest.fixture
def slow_stub_bedrock(monkeypatch: pytest.MonkeyPatch) -> _SlowStubBedrockClient:
    stub = _SlowStubBedrockClient()
    monkeypatch.setattr(embed_question_module, "bedrock_client", stub)
    monkeypatch.setattr(generate_answer_module, "bedrock_client", stub)
    monkeypatch.setattr(condense_history_module, "bedrock_client", stub)
    monkeypatch.setattr(preprocess_input_module, "bedrock_client", stub)
    return stub


@pytest.fixture
def similarity_stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[SimilarityMatch]]:
    holder: dict[str, list[SimilarityMatch]] = {"matches": []}

    async def _stub(
        self: KnowledgeChunkRepository, query_embedding: list[float], *, top_k: int
    ) -> list[SimilarityMatch]:
        return holder["matches"]

    monkeypatch.setattr(KnowledgeChunkRepository, "similarity_search", _stub)
    return holder


@pytest.fixture(autouse=True)
def _isolated_shutdown_state(monkeypatch: pytest.MonkeyPatch) -> ShutdownState:
    """`chat_service._stream_chat_graph` calls the module-level `track_stream` (bound to
    the shared `shutdown_state` singleton by default) — a fresh instance per test avoids
    cross-test leakage of the active-stream counter."""
    fresh = ShutdownState()
    monkeypatch.setattr(chat_service, "track_stream", lambda: track_stream(fresh))
    return fresh


async def test_slow_stream_is_tracked_and_drains_cleanly(
    db_session: AsyncSession,
    slow_stub_bedrock: _SlowStubBedrockClient,
    similarity_stub: dict[str, list[SimilarityMatch]],
    _isolated_shutdown_state: ShutdownState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    similarity_stub["matches"] = [
        SimilarityMatch(
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            content="Konten relevan.",
            page_number=None,
            score=0.99,
            title="Dok",
            source_url=None,
            valid_until=None,
            superseded_by_title=None,
        )
    ]
    monkeypatch_threshold = settings.SIMILARITY_SCORE_THRESHOLD
    settings.SIMILARITY_SCORE_THRESHOLD = 0.0
    try:
        session_row = await SessionRepository(db_session).create(
            Session(user_id="test-user", persona="user")
        )
        await db_session.flush()

        # `_stream_chat_graph` calls `await session.commit()` mid-stream (docs/06-api-
        # specification.md §0 needs the assistant turn durably persisted before the
        # `done` event). Left as a real commit, that would permanently write this
        # session/message/usage_metric row into the shared dev database (unlike
        # `tests/integration/test_user_chat_graph.py`, which invokes the graph directly
        # and never commits) — no-op it here so `db_session`'s teardown rollback fully
        # reverts everything this test creates, same isolation guarantee as every other
        # test in this suite.

        async def _noop_commit() -> None:
            return None

        monkeypatch.setattr(db_session, "commit", _noop_commit)

        events: list[str] = []

        async def _consume() -> None:
            async for event in chat_service.stream_user_chat_response(
                db_session,
                session_id=session_row.session_id,
                user_id="test-user",
                original_question="Pertanyaan tentang produk",
                question="Pertanyaan tentang produk",
                image_bytes=None,
                image_format=None,
            ):
                events.append(event)

        consumer_task = asyncio.ensure_future(_consume())
        await asyncio.sleep(0.05)  # let the stream reach the mid-generation await point

        assert _isolated_shutdown_state.active_stream_count == 1

        drain_task = asyncio.ensure_future(
            _isolated_shutdown_state.wait_drained(timeout_seconds=5.0)
        )
        await asyncio.sleep(0.01)
        assert not drain_task.done()  # still waiting: the stream hasn't finished yet

        slow_stub_bedrock.release.set()  # let the "generation" finish, as if SIGTERM's
        # grace period elapsed and the in-flight response was allowed to complete.
        await consumer_task

        assert _isolated_shutdown_state.active_stream_count == 0
        assert await drain_task is True
        assert any('"type":"done"' in event for event in events)
    finally:
        settings.SIMILARITY_SCORE_THRESHOLD = monkeypatch_threshold
