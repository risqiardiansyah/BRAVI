"""Integration tests for `app.graphs.ingestion_graph` — docs/05-ai-agent-design.md §3.2,
docs/12-testing-strategy.md §3 ("ingestion_graph full run: file ingestion, text
ingestion, and failure path (corrupt PDF, unreachable URL)"), docs/IMPLEMENTATION_PLAN.md
Phase 6.

Bedrock is mocked (module-level `bedrock_client` rebound in `app.graphs.nodes.embed_chunks`,
mirroring `tests/integration/test_health.py`'s pattern of rebinding rather than mutating the
real singleton); the database is the same live test Postgres+pgvector instance
Phase 2's repository tests already require. Each test runs inside a transaction rolled
back at fixture teardown (`db_session`), so nothing persists across test runs.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import normalize_asyncpg_url
from app.graphs import ingestion_graph as ingestion_graph_module
from app.graphs.ingestion_state import IngestionState
from app.graphs.nodes import embed_chunks as embed_chunks_module
from app.models.ingestion_job import IngestionJob
from app.models.knowledge_document import KnowledgeDocument
from app.repositories.ingestion_job_repository import IngestionJobRepository
from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from tests.pdf_fixtures import CORRUPT_PDF_BYTES, build_minimal_pdf


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Mirrors tests/unit/test_repositories.py's fixture — its own throwaway engine
    on this test's own event loop, rolled back (never committed) at teardown."""
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


class _StubBedrockClient:
    """Rebinds the `bedrock_client` name inside `embed_chunks`'s module namespace
    (not the real global singleton) — same pattern as
    tests/integration/test_health.py's `_StubBedrockClient`/`_StubRedisClient`."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    async def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        self.calls.append((texts, input_type))
        return [[0.01] * 1024 for _ in texts]


@pytest.fixture
def stub_bedrock(monkeypatch: pytest.MonkeyPatch) -> _StubBedrockClient:
    stub = _StubBedrockClient()
    monkeypatch.setattr(embed_chunks_module, "bedrock_client", stub)
    return stub


async def _make_document_and_job(
    session: AsyncSession, *, source_type: str
) -> tuple[uuid.UUID, uuid.UUID]:
    document = await KnowledgeDocumentRepository(session).create(
        KnowledgeDocument(source_type=source_type, title="Test Doc", status="queued")
    )
    job = await IngestionJobRepository(session).create(
        IngestionJob(job_type="on_demand", document_id=document.id, status="processing")
    )
    await session.flush()
    return document.id, job.id


async def _run_graph(session: AsyncSession, initial_state: IngestionState) -> IngestionState:
    result = await ingestion_graph_module.ingestion_graph.ainvoke(
        dict(initial_state), config={"configurable": {"session": session}}
    )
    return result  # type: ignore[no-any-return]


# --- Happy path: raw text -----------------------------------------------------


async def test_text_ingestion_happy_path(
    db_session: AsyncSession, stub_bedrock: _StubBedrockClient
) -> None:
    document_id, job_id = await _make_document_and_job(db_session, source_type="text")
    text = "Halo, ini dokumen pengujian ingestion berbasis teks mentah. " * 40

    final_state = await _run_graph(
        db_session,
        {
            "source_type": "text",
            "source_ref": text,
            "document_id": document_id,
            "job_id": job_id,
        },
    )

    assert final_state["status"] == "completed"
    assert len(stub_bedrock.calls) >= 1
    assert all(call[1] == "search_document" for call in stub_bedrock.calls)

    chunks = await KnowledgeChunkRepository(db_session).list_all()
    doc_chunks = [c for c in chunks if c.document_id == document_id]
    assert len(doc_chunks) >= 1
    assert all(c.page_number is None for c in doc_chunks)

    document = await KnowledgeDocumentRepository(db_session).get_by_id(document_id)
    assert document is not None
    assert document.status == "completed"
    assert document.chunk_count == len(doc_chunks)

    job = await IngestionJobRepository(db_session).get_by_id(job_id)
    assert job is not None
    assert job.status == "completed"
    assert job.completed_at is not None


# --- Happy path: PDF file, multi-page (page metadata preserved) --------------


async def test_file_ingestion_happy_path_preserves_page_numbers(
    db_session: AsyncSession, stub_bedrock: _StubBedrockClient, tmp_path: Path
) -> None:
    pdf_bytes = build_minimal_pdf(
        [
            "Halaman pertama membahas kebijakan pengembalian produk secara umum.",
            "Halaman kedua membahas prosedur eskalasi ke tim dukungan pelanggan.",
        ]
    )
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(pdf_bytes)

    document_id, job_id = await _make_document_and_job(db_session, source_type="file")

    final_state = await _run_graph(
        db_session,
        {
            "source_type": "file",
            "source_ref": str(pdf_path),
            "document_id": document_id,
            "job_id": job_id,
        },
    )

    assert final_state["status"] == "completed"

    chunks = await KnowledgeChunkRepository(db_session).list_all()
    doc_chunks = sorted(
        (c for c in chunks if c.document_id == document_id), key=lambda c: c.chunk_index
    )
    assert len(doc_chunks) >= 2
    page_numbers = {c.page_number for c in doc_chunks}
    assert page_numbers == {1, 2}


# --- Failure path: corrupt PDF -------------------------------------------------


async def test_corrupt_pdf_fails_gracefully_without_bedrock_call(
    db_session: AsyncSession, stub_bedrock: _StubBedrockClient, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "corrupt.pdf"
    pdf_path.write_bytes(CORRUPT_PDF_BYTES)

    document_id, job_id = await _make_document_and_job(db_session, source_type="file")

    final_state = await _run_graph(
        db_session,
        {
            "source_type": "file",
            "source_ref": str(pdf_path),
            "document_id": document_id,
            "job_id": job_id,
        },
    )

    assert final_state["status"] == "failed"
    assert final_state.get("error")
    assert stub_bedrock.calls == []  # never reached embed_chunks

    document = await KnowledgeDocumentRepository(db_session).get_by_id(document_id)
    assert document is not None
    assert document.status == "failed"
    assert document.error_message

    job = await IngestionJobRepository(db_session).get_by_id(job_id)
    assert job is not None
    assert job.status == "failed"

    chunks = await KnowledgeChunkRepository(db_session).list_all()
    assert not any(c.document_id == document_id for c in chunks)


# --- Failure path: unreachable URL --------------------------------------------


async def test_unreachable_url_fails_gracefully_without_bedrock_call(
    db_session: AsyncSession, stub_bedrock: _StubBedrockClient
) -> None:
    document_id, job_id = await _make_document_and_job(db_session, source_type="url")

    final_state = await _run_graph(
        db_session,
        {
            "source_type": "url",
            "source_ref": "http://127.0.0.1:1/nonexistent",
            "document_id": document_id,
            "job_id": job_id,
        },
    )

    assert final_state["status"] == "failed"
    assert final_state.get("error")
    assert stub_bedrock.calls == []

    document = await KnowledgeDocumentRepository(db_session).get_by_id(document_id)
    assert document is not None
    assert document.status == "failed"

    job = await IngestionJobRepository(db_session).get_by_id(job_id)
    assert job is not None
    assert job.status == "failed"
