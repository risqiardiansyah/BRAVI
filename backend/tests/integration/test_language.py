"""Response-language regression test — docs/12-testing-strategy.md §3 ("assert generated
answers... and every canned response are the documented Bahasa Indonesia text, for both an
Indonesian-phrased and an English-phrased input question"), docs/05-ai-agent-design.md §1/§4.

Canned (short-circuit) responses are asserted verbatim against the canonical text
(docs/prompts/ai-agent.md §3-§5) regardless of question language. For the full-RAG path,
since a mocked Bedrock call cannot itself prove what language a real model would answer in,
this asserts the one thing that actually determines it: the exact "Selalu jawab dalam Bahasa
Indonesia" instruction is present in the system prompt sent to `generate_stream`, identically
whether the question was phrased in Indonesian or English.
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
from app.graphs import user_chat_graph as user_chat_graph_module
from app.graphs.canned_responses import GREETING_RESPONSE
from app.graphs.chat_state import ChatState
from app.graphs.nodes import embed_question as embed_question_module
from app.graphs.nodes import generate_answer as generate_answer_module
from app.models.session import Session
from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository, SimilarityMatch
from app.repositories.session_repository import SessionRepository

_LANGUAGE_INSTRUCTION = "Selalu jawab dalam Bahasa Indonesia"


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
        for token in ["Jawaban ", "dalam ", "Bahasa Indonesia."]:
            yield token


@pytest.fixture
def stub_bedrock(monkeypatch: pytest.MonkeyPatch) -> _StubBedrockClient:
    stub = _StubBedrockClient()
    monkeypatch.setattr(embed_question_module, "bedrock_client", stub)
    monkeypatch.setattr(generate_answer_module, "bedrock_client", stub)

    async def _stub_similarity_search(
        self: KnowledgeChunkRepository, query_embedding: list[float], *, top_k: int
    ) -> list[SimilarityMatch]:
        return [
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

    monkeypatch.setattr(KnowledgeChunkRepository, "similarity_search", _stub_similarity_search)
    return stub


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


@pytest.mark.parametrize(
    "question",
    ["Halo", "hello"],
    ids=["indonesian_greeting", "english_greeting"],
)
async def test_greeting_canned_response_is_always_bahasa_indonesia(
    db_session: AsyncSession, stub_bedrock: _StubBedrockClient, question: str
) -> None:
    session_row = await _make_session(db_session)
    final_state = await user_chat_graph_module.user_chat_graph.ainvoke(
        dict(_initial_state(question, session_id=session_row.session_id)),
        config={"configurable": {"session": db_session}},
    )
    assert final_state["answer"] == GREETING_RESPONSE


@pytest.mark.parametrize(
    "question",
    [
        "Apa syarat pengajuan klaim asuransi kesehatan?",
        "What are the requirements to file a health insurance claim?",
    ],
    ids=["indonesian_question", "english_question"],
)
async def test_full_rag_system_prompt_always_instructs_bahasa_indonesia(
    db_session: AsyncSession, stub_bedrock: _StubBedrockClient, question: str
) -> None:
    session_row = await _make_session(db_session)
    await user_chat_graph_module.user_chat_graph.ainvoke(
        dict(_initial_state(question, session_id=session_row.session_id)),
        config={"configurable": {"session": db_session}},
    )

    assert len(stub_bedrock.generate_calls) == 1
    system_prompt = stub_bedrock.generate_calls[0].system
    assert system_prompt is not None
    assert _LANGUAGE_INSTRUCTION in system_prompt
