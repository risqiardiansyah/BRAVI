"""Integration tests for `app.jobs.run_initial_ingestion` — docs/07-database-design.md
§5 ("Idempotency Strategy for Startup Ingestion"), docs/IMPLEMENTATION_PLAN.md Phase 6
Definition of Done ("Running the startup job twice against the same source list
creates no duplicate `knowledge_documents` rows").

Uses a throwaway `NullPool` engine bound to this test's own event loop
(`session_factory`), swapped in for the job module's `AsyncSessionLocal` — mirroring
`tests/integration/test_health.py`'s `test_check_database_returns_false_for_unreachable_host`
pattern of rebinding `AsyncSessionLocal` rather than reusing the app's real pooled
singleton, since pytest-asyncio gives each test function its own event loop and
asyncpg connections cannot be reused across loops. `run_initial_ingestion(source_ids=...)`
scopes every run to only the one `knowledge_sources` row this test seeds, so this
suite never touches any other (e.g. real, operator-managed) row already present in
the database; the fixture explicitly deletes everything it created afterward.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import normalize_asyncpg_url
from app.graphs.nodes import embed_chunks as embed_chunks_module
from app.jobs import run_initial_ingestion as job_module
from app.models.ingestion_job import IngestionJob
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.models.knowledge_source import KnowledgeSource
from app.repositories.knowledge_source_repository import KnowledgeSourceRepository
from tests.pdf_fixtures import build_minimal_pdf


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class _StubBedrockClient:
    def __init__(self) -> None:
        self.call_count = 0

    async def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        self.call_count += 1
        return [[0.02] * 1024 for _ in texts]


@pytest.fixture
def stub_bedrock(monkeypatch: pytest.MonkeyPatch) -> _StubBedrockClient:
    stub = _StubBedrockClient()
    monkeypatch.setattr(embed_chunks_module, "bedrock_client", stub)
    return stub


@pytest_asyncio.fixture
async def session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(job_module, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_source(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[uuid.UUID, None]:
    relative_path = _unique("docs/idempotency-test") + ".pdf"
    async with session_factory() as session:
        source = await KnowledgeSourceRepository(session).create(
            KnowledgeSource(relative_path=relative_path)
        )
        await session.commit()
        source_id = source.id

    yield source_id

    async with session_factory() as session:
        document_ids = list(
            (
                await session.execute(
                    select(KnowledgeDocument.id).where(KnowledgeDocument.source_id == source_id)
                )
            )
            .scalars()
            .all()
        )
        if document_ids:
            await session.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.document_id.in_(document_ids))
            )
            await session.execute(
                delete(IngestionJob).where(IngestionJob.document_id.in_(document_ids))
            )
            await session.execute(
                delete(KnowledgeDocument).where(KnowledgeDocument.id.in_(document_ids))
            )
        await session.execute(delete(KnowledgeSource).where(KnowledgeSource.id == source_id))
        await session.commit()


async def _documents_for_source(
    session_factory: async_sessionmaker[AsyncSession], source_id: uuid.UUID
) -> list[KnowledgeDocument]:
    async with session_factory() as session:
        result = await session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.source_id == source_id)
        )
        return list(result.scalars().all())


async def test_two_runs_against_unchanged_content_create_no_duplicate_documents(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_source: uuid.UUID,
    stub_bedrock: _StubBedrockClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf_bytes = build_minimal_pdf(["Konten sumber pengujian idempotensi ingestion startup."])

    async def fake_download(_url: str) -> bytes:
        return pdf_bytes

    monkeypatch.setattr(job_module, "download_bytes", fake_download)

    first_outcomes = await job_module.run_initial_ingestion(source_ids=[seeded_source])
    assert len(first_outcomes) == 1
    assert first_outcomes[0].outcome == "completed"

    documents_after_first = await _documents_for_source(session_factory, seeded_source)
    assert len(documents_after_first) == 1
    assert documents_after_first[0].status == "completed"
    chunk_count_after_first = documents_after_first[0].chunk_count
    assert chunk_count_after_first > 0
    embed_calls_after_first = stub_bedrock.call_count

    second_outcomes = await job_module.run_initial_ingestion(source_ids=[seeded_source])
    assert len(second_outcomes) == 1
    assert second_outcomes[0].outcome == "skipped_unchanged"

    documents_after_second = await _documents_for_source(session_factory, seeded_source)
    # Still exactly one knowledge_documents row — no duplicate created.
    assert len(documents_after_second) == 1
    assert documents_after_second[0].id == documents_after_first[0].id
    assert documents_after_second[0].chunk_count == chunk_count_after_first
    # The unchanged-content run never re-embedded anything.
    assert stub_bedrock.call_count == embed_calls_after_first


async def test_changed_content_is_reingested_as_a_new_document(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_source: uuid.UUID,
    stub_bedrock: _StubBedrockClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_pdf = build_minimal_pdf(["Versi pertama dari dokumen sumber ini."])
    second_pdf = build_minimal_pdf(["Versi kedua dengan konten yang telah diperbarui sepenuhnya."])
    call_state = {"n": 0}

    async def fake_download(_url: str) -> bytes:
        call_state["n"] += 1
        return first_pdf if call_state["n"] == 1 else second_pdf

    monkeypatch.setattr(job_module, "download_bytes", fake_download)

    first_outcomes = await job_module.run_initial_ingestion(source_ids=[seeded_source])
    assert first_outcomes[0].outcome == "completed"
    documents_after_first = await _documents_for_source(session_factory, seeded_source)
    assert len(documents_after_first) == 1

    second_outcomes = await job_module.run_initial_ingestion(source_ids=[seeded_source])
    assert second_outcomes[0].outcome == "completed"  # re-ingested, not skipped

    documents_after_second = await _documents_for_source(session_factory, seeded_source)
    assert len(documents_after_second) == 2  # a new knowledge_documents row was created
    assert {d.status for d in documents_after_second} == {"completed"}


async def test_new_source_download_failure_records_a_failed_document(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_source: uuid.UUID,
    stub_bedrock: _StubBedrockClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_download(_url: str) -> bytes:
        raise RuntimeError("simulated unreachable host")

    monkeypatch.setattr(job_module, "download_bytes", failing_download)

    outcomes = await job_module.run_initial_ingestion(source_ids=[seeded_source])
    assert outcomes[0].outcome == "failed"

    documents = await _documents_for_source(session_factory, seeded_source)
    assert len(documents) == 1
    assert documents[0].status == "failed"
    assert documents[0].error_message
    assert stub_bedrock.call_count == 0
