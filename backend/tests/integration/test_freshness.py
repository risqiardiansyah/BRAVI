"""Answer freshness/versioning test — docs/12-testing-strategy.md §3 ("retrieved chunk with
valid_until in the past or superseded_by_document_id set -> prompt context includes that
metadata... retrieved chunk with neither set -> assert the context passed to the model
contains no freshness metadata"), docs/05-ai-agent-design.md §2.3/§4,
docs/07-database-design.md §5b.

A mocked Bedrock response can't itself prove a real model would mention this in its answer
text — the concrete, testable contract is what `05-ai-agent-design.md` §4 actually specifies:
`similarity_search`/`render_context` attach `valid_until`/`superseded_by_title` to the
`<context>` block only when present on that document, never fabricating it otherwise. This
is tested both as a pure unit test (`render_context`) and end-to-end through the graph
(asserting the exact system prompt sent to `generate_stream`).
"""

from __future__ import annotations

import datetime
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
from app.graphs import user_chat_graph as user_chat_graph_module
from app.graphs.chat_state import ChatState, TopMatch
from app.graphs.nodes import embed_question as embed_question_module
from app.graphs.nodes import generate_answer as generate_answer_module
from app.graphs.prompts import render_context
from app.models.session import Session
from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository, SimilarityMatch
from app.repositories.session_repository import SessionRepository


def _top_match(**overrides: Any) -> TopMatch:
    base: TopMatch = {
        "chunk_id": uuid.uuid4(),
        "document_id": uuid.uuid4(),
        "content": "Konten dokumen contoh.",
        "score": 0.9,
        "page_number": 1,
        "title": "Dokumen Contoh",
        "source_url": None,
        "valid_until": None,
        "superseded_by_title": None,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


# --- Unit: render_context ----------------------------------------------------------------


def test_render_context_includes_valid_until_when_set() -> None:
    match = _top_match(valid_until=datetime.date(2020, 1, 1))
    block = render_context([match])
    assert "valid_until: 2020-01-01" in block


def test_render_context_includes_superseded_by_when_set() -> None:
    match = _top_match(superseded_by_title="Dokumen Terbaru")
    block = render_context([match])
    assert "superseded_by: Dokumen Terbaru" in block


def test_render_context_omits_freshness_metadata_when_absent() -> None:
    match = _top_match()
    block = render_context([match])
    assert "valid_until" not in block
    assert "superseded_by" not in block


# --- Integration: through the graph, into the actual system prompt sent to Bedrock -------


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
        self.generate_calls: list[PromptPayload] = []

    async def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        return [[0.01] * 1024 for _ in texts]

    async def generate_stream(self, prompt: PromptPayload, **_params: Any) -> Any:
        self.generate_calls.append(prompt)
        yield "Jawaban."


def _install_similarity_stub(monkeypatch: pytest.MonkeyPatch, match: SimilarityMatch) -> None:
    async def _stub(
        self: KnowledgeChunkRepository, query_embedding: list[float], *, top_k: int
    ) -> list[SimilarityMatch]:
        return [match]

    monkeypatch.setattr(KnowledgeChunkRepository, "similarity_search", _stub)


async def _make_session(db_session: AsyncSession) -> Session:
    session_row = await SessionRepository(db_session).create(
        Session(user_id="test-user", persona="user")
    )
    await db_session.flush()
    return session_row


def _initial_state(question: str, *, session_id: Any) -> ChatState:
    return {
        "session_id": session_id,
        "user_id": "test-user",
        "persona": "user",
        "question": question,
        "original_question": question,
        "image_bytes": None,
        "image_format": None,
        "short_circuited": False,
        "short_circuit_reason": None,
        "mode": None,
    }


async def test_expired_document_metadata_reaches_system_prompt(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubBedrockClient()
    monkeypatch.setattr(embed_question_module, "bedrock_client", stub)
    monkeypatch.setattr(generate_answer_module, "bedrock_client", stub)
    _install_similarity_stub(
        monkeypatch,
        SimilarityMatch(
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            content="Kebijakan lama.",
            page_number=1,
            score=0.9,
            title="Kebijakan 2019",
            source_url=None,
            valid_until=datetime.date(2020, 1, 1),
            superseded_by_title=None,
        ),
    )
    session_row = await _make_session(db_session)

    await user_chat_graph_module.user_chat_graph.ainvoke(
        dict(_initial_state("Apa isi kebijakan ini?", session_id=session_row.session_id)),
        config={"configurable": {"session": db_session}},
    )

    system_prompt = stub.generate_calls[0].system
    assert system_prompt is not None
    assert "valid_until: 2020-01-01" in system_prompt


async def test_document_without_metadata_has_no_freshness_mention_in_system_prompt(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubBedrockClient()
    monkeypatch.setattr(embed_question_module, "bedrock_client", stub)
    monkeypatch.setattr(generate_answer_module, "bedrock_client", stub)
    _install_similarity_stub(
        monkeypatch,
        SimilarityMatch(
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            content="Kebijakan berlaku.",
            page_number=1,
            score=0.9,
            title="Kebijakan Umum",
            source_url=None,
            valid_until=None,
            superseded_by_title=None,
        ),
    )
    session_row = await _make_session(db_session)

    await user_chat_graph_module.user_chat_graph.ainvoke(
        dict(_initial_state("Apa isi kebijakan ini?", session_id=session_row.session_id)),
        config={"configurable": {"session": db_session}},
    )

    system_prompt = stub.generate_calls[0].system
    assert system_prompt is not None
    assert "valid_until" not in system_prompt.split("<context>")[1].split("</context>")[0]
    assert "superseded_by" not in system_prompt.split("<context>")[1].split("</context>")[0]
