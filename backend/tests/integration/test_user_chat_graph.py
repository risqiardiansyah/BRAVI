"""Integration tests for `app.graphs.user_chat_graph` — docs/05-ai-agent-design.md §2.2,
docs/12-testing-strategy.md §3 ("all four short-circuit tiers + full RAG... asserting zero
Bedrock text calls for the first three tiers"), docs/IMPLEMENTATION_PLAN.md Phase 9.

Bedrock is stubbed at each node module's own `bedrock_client` binding (mirrors
`tests/integration/test_ingestion_graph.py`'s pattern). `KnowledgeChunkRepository.
similarity_search` is stubbed at the class level rather than seeding real
`knowledge_chunks` rows — this environment's shared test database already carries real,
intentionally-preserved rows from earlier phases' manual verification (see `SESSION.md`),
so asserting deterministic pgvector distances against them would be flaky; stubbing this
one repository call isolates the graph's own routing/short-circuit logic (what this test
actually verifies) from real embedding content, while `sessions`/`messages`/`usage_metrics`
persistence still goes through the real test database to prove `persist_message`/
`log_metrics` work end-to-end.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.clients.bedrock_client import PromptPayload
from app.config import settings
from app.db import normalize_asyncpg_url
from app.graphs import user_chat_graph as user_chat_graph_module
from app.graphs.chat_state import ChatState
from app.graphs.nodes import condense_history as condense_history_module
from app.graphs.nodes import embed_question as embed_question_module
from app.graphs.nodes import generate_answer as generate_answer_module
from app.graphs.nodes import preprocess_input as preprocess_input_module
from app.models.session import Session
from app.models.usage_metric import UsageMetric
from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository, SimilarityMatch
from app.repositories.message_repository import MessageRepository
from app.repositories.session_repository import SessionRepository


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Own throwaway `NullPool` engine, rolled back (never committed) at teardown —
    mirrors `tests/integration/test_ingestion_graph.py`'s `db_session` fixture."""
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


class _StubBedrockClient:
    def __init__(self, answer_tokens: list[str] | None = None) -> None:
        self.embed_calls: list[tuple[list[str], str]] = []
        self.generate_calls: list[PromptPayload] = []
        self.answer_tokens = answer_tokens or ["Ini ", "adalah ", "jawaban."]

    async def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        self.embed_calls.append((list(texts), input_type))
        return [[0.01] * 1024 for _ in texts]

    async def generate_stream(self, prompt: PromptPayload, **_params: Any) -> Any:
        self.generate_calls.append(prompt)
        for token in self.answer_tokens:
            yield token


@pytest.fixture
def stub_bedrock(monkeypatch: pytest.MonkeyPatch) -> _StubBedrockClient:
    stub = _StubBedrockClient()
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


async def _make_session(db_session: AsyncSession, *, user_id: str = "test-user") -> Session:
    session_row = await SessionRepository(db_session).create(
        Session(user_id=user_id, persona="user")
    )
    await db_session.flush()
    return session_row


def _initial_state(question: str, *, session_id: Any, user_id: str) -> ChatState:
    return {
        "session_id": session_id,
        "user_id": user_id,
        "persona": "user",
        "question": question,
        "original_question": question,
        "image_bytes": None,
        "image_format": None,
        "short_circuited": False,
        "short_circuit_reason": None,
        "mode": None,
    }


async def _run_graph(db_session: AsyncSession, initial_state: ChatState) -> ChatState:
    result = await user_chat_graph_module.user_chat_graph.ainvoke(
        dict(initial_state), config={"configurable": {"session": db_session}}
    )
    return result  # type: ignore[no-any-return]


# --- Tier 1: greeting -----------------------------------------------------------------


async def test_greeting_short_circuits_with_no_bedrock_calls(
    db_session: AsyncSession, stub_bedrock: _StubBedrockClient
) -> None:
    session_row = await _make_session(db_session)
    final_state = await _run_graph(
        db_session, _initial_state("Halo", session_id=session_row.session_id, user_id="test-user")
    )

    assert final_state["short_circuited"] is True
    assert final_state["short_circuit_reason"] == "greeting"
    assert final_state["mode"] is None
    assert stub_bedrock.embed_calls == []
    assert stub_bedrock.generate_calls == []

    messages = await MessageRepository(db_session).list_by_session_id(session_row.session_id)
    assert [m.role for m in messages] == ["assistant"]
    assert messages[0].content == final_state["answer"]

    metrics = (
        (
            await db_session.execute(
                select(UsageMetric).where(UsageMetric.session_id == session_row.session_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(metrics) == 1
    assert metrics[0].short_circuit_reason == "greeting"
    assert metrics[0].short_circuited is True
    assert metrics[0].ttft_ms is None


# --- Tier 2: out-of-topic ---------------------------------------------------------------


async def test_out_of_topic_short_circuits_with_no_bedrock_calls(
    db_session: AsyncSession, stub_bedrock: _StubBedrockClient
) -> None:
    session_row = await _make_session(db_session)
    final_state = await _run_graph(
        db_session,
        _initial_state(
            "Tolong buatkan puisi tentang cinta",
            session_id=session_row.session_id,
            user_id="test-user",
        ),
    )

    assert final_state["short_circuited"] is True
    assert final_state["short_circuit_reason"] == "out_of_topic"
    assert final_state["mode"] is None
    assert stub_bedrock.embed_calls == []
    assert stub_bedrock.generate_calls == []


# --- Tier 3: below similarity threshold --------------------------------------------------


async def test_low_similarity_short_circuits_after_one_embed_call_only(
    db_session: AsyncSession,
    stub_bedrock: _StubBedrockClient,
    similarity_stub: dict[str, list[SimilarityMatch]],
) -> None:
    similarity_stub["matches"] = []  # no matches at all -> best_score is None
    session_row = await _make_session(db_session)

    final_state = await _run_graph(
        db_session,
        _initial_state(
            "Apa syarat pengajuan klaim asuransi kesehatan?",
            session_id=session_row.session_id,
            user_id="test-user",
        ),
    )

    assert final_state["short_circuited"] is True
    assert final_state["short_circuit_reason"] == "low_similarity"
    assert final_state["mode"] is None
    assert len(stub_bedrock.embed_calls) == 1
    assert stub_bedrock.generate_calls == []


# --- Tier 4: full RAG ---------------------------------------------------------------------


async def test_full_rag_generates_grounded_answer_with_sources(
    db_session: AsyncSession,
    stub_bedrock: _StubBedrockClient,
    similarity_stub: dict[str, list[SimilarityMatch]],
) -> None:
    similarity_stub["matches"] = [
        SimilarityMatch(
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            content="Klaim dapat diajukan dalam 30 hari kerja.",
            page_number=2,
            score=0.9,
            title="Panduan Klaim",
            source_url="https://example.com/panduan-klaim",
            valid_until=None,
            superseded_by_title=None,
        )
    ]
    session_row = await _make_session(db_session)

    final_state = await _run_graph(
        db_session,
        _initial_state(
            "Apa syarat pengajuan klaim asuransi kesehatan?",
            session_id=session_row.session_id,
            user_id="test-user",
        ),
    )

    assert final_state["short_circuited"] is False
    assert final_state["short_circuit_reason"] is None
    assert final_state["mode"] is None
    assert len(stub_bedrock.embed_calls) == 1
    assert len(stub_bedrock.generate_calls) == 1
    assert final_state["ttft_ms"] is not None
    assert final_state["ttft_ms"] >= 0

    answer = final_state["answer"]
    assert "Ini adalah jawaban." in answer
    assert "## Sources" in answer
    assert "[Panduan Klaim](https://example.com/panduan-klaim)" in answer

    sources = final_state["sources"]
    assert len(sources) == 1
    assert sources[0]["title"] == "Panduan Klaim"

    messages = await MessageRepository(db_session).list_by_session_id(session_row.session_id)
    assert [m.role for m in messages] == ["assistant"]
    assert messages[0].content == answer
