"""Add-knowledge-intent regression test — docs/06-api-specification.md §5,
docs/12-testing-strategy.md §3 ("the same question sent to user_chat_graph must fall
through to normal QA handling (never the template) — this is also a persona-isolation
check, not just a functional one"), docs/IMPLEMENTATION_PLAN.md Phase 10.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.clients.bedrock_client import PromptPayload
from app.config import settings
from app.db import normalize_asyncpg_url
from app.graphs import operator_chat_graph as operator_chat_graph_module
from app.graphs import user_chat_graph as user_chat_graph_module
from app.graphs.canned_responses import ADD_KNOWLEDGE_INTENT_RESPONSE
from app.graphs.chat_state import ChatState
from app.graphs.nodes import embed_question as embed_question_module
from app.graphs.nodes import generate_answer as generate_answer_module
from app.models.session import Session
from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository, SimilarityMatch
from app.repositories.session_repository import SessionRepository

_TRIGGER_PHRASE_ID = "tambah knowledge ai"
_TRIGGER_PHRASE_EN = "add ai knowledge"


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
    def __init__(self) -> None:
        self.embed_calls: list[tuple[list[str], str]] = []
        self.generate_calls: list[PromptPayload] = []

    async def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        self.embed_calls.append((list(texts), input_type))
        return [[0.01] * 1024 for _ in texts]

    async def generate_stream(self, prompt: PromptPayload, **_params: Any) -> Any:
        self.generate_calls.append(prompt)
        for token in ["Ini ", "adalah ", "jawaban."]:
            yield token


@pytest.fixture
def stub_bedrock(monkeypatch: pytest.MonkeyPatch) -> _StubBedrockClient:
    stub = _StubBedrockClient()
    monkeypatch.setattr(embed_question_module, "bedrock_client", stub)
    monkeypatch.setattr(generate_answer_module, "bedrock_client", stub)
    return stub


@pytest.fixture
def similarity_stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[SimilarityMatch]]:
    holder: dict[str, list[SimilarityMatch]] = {
        "matches": [
            SimilarityMatch(
                chunk_id=uuid.uuid4(),
                document_id=uuid.uuid4(),
                content="Klaim dapat diajukan dalam 30 hari kerja.",
                page_number=1,
                score=0.9,
                title="Panduan Klaim",
                source_url=None,
                valid_until=None,
                superseded_by_title=None,
            )
        ]
    }

    async def _stub(
        self: KnowledgeChunkRepository, query_embedding: list[float], *, top_k: int
    ) -> list[SimilarityMatch]:
        return holder["matches"]

    monkeypatch.setattr(KnowledgeChunkRepository, "similarity_search", _stub)
    return holder


async def _make_session(db_session: AsyncSession, *, persona: str) -> Session:
    session_row = await SessionRepository(db_session).create(
        Session(user_id="test-user", persona=persona)
    )
    await db_session.flush()
    return session_row


def _initial_state(question: str, *, session_id: Any, persona: str) -> ChatState:
    return {
        "session_id": session_id,
        "user_id": "test-user",
        "persona": persona,
        "question": question,
        "original_question": question,
        "image_bytes": None,
        "image_format": None,
        "short_circuited": False,
        "short_circuit_reason": None,
        "mode": None,
    }


@pytest.mark.parametrize(
    "phrase",
    [_TRIGGER_PHRASE_ID, _TRIGGER_PHRASE_EN],
    ids=["indonesian_phrase", "english_phrase"],
)
async def test_operator_add_knowledge_intent_returns_exact_template_with_no_bedrock_call(
    db_session: AsyncSession, stub_bedrock: _StubBedrockClient, phrase: str
) -> None:
    session_row = await _make_session(db_session, persona="operator")

    final_state = await operator_chat_graph_module.operator_chat_graph.ainvoke(
        dict(_initial_state(phrase, session_id=session_row.session_id, persona="operator")),
        config={"configurable": {"session": db_session}},
    )

    assert final_state["answer"] == ADD_KNOWLEDGE_INTENT_RESPONSE
    assert final_state["short_circuited"] is True
    assert final_state["short_circuit_reason"] == "add_knowledge_intent"
    assert final_state["mode"] is None
    assert stub_bedrock.embed_calls == []
    assert stub_bedrock.generate_calls == []


async def test_same_phrase_via_user_chat_graph_falls_through_to_normal_qa(
    db_session: AsyncSession,
    stub_bedrock: _StubBedrockClient,
    similarity_stub: dict[str, list[SimilarityMatch]],
) -> None:
    """`user_chat_graph` has no `classify_add_knowledge_intent` node at all
    (docs/11-coding-standard.md §8.1) — the identical trigger phrase must fall through
    every short-circuit tier to full RAG instead of ever seeing the template."""
    session_row = await _make_session(db_session, persona="user")

    final_state = await user_chat_graph_module.user_chat_graph.ainvoke(
        dict(_initial_state(_TRIGGER_PHRASE_ID, session_id=session_row.session_id, persona="user")),
        config={"configurable": {"session": db_session}},
    )

    assert final_state["answer"] != ADD_KNOWLEDGE_INTENT_RESPONSE
    assert final_state["short_circuit_reason"] != "add_knowledge_intent"
    assert final_state["short_circuited"] is False
    assert len(stub_bedrock.embed_calls) == 1
    assert len(stub_bedrock.generate_calls) == 1
