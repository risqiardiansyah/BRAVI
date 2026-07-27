"""Direct unit tests for `app.services.ingestion_service` —
docs/06-api-specification.md §6/§7/§7.1, docs/07-database-design.md §5a/§5b,
docs/IMPLEMENTATION_PLAN.md Phase 14 ("Coverage targets met per
12-testing-strategy.md §8").

`tests/integration/test_ingest_endpoint.py`/`test_knowledge_delete.py` already cover
these code paths *functionally* end-to-end via `TestClient`, but `TestClient` drives
the ASGI app through its own dedicated event-loop thread (`anyio`'s blocking portal)
whenever called from an `async def` test — a thread never registered with
`coverage.py` (which only traces new threads when `[tool.coverage.run]
concurrency = thread` is set, which this project does not set). That leaves several
of `ingestion_service.py`'s own statements looking uncovered in `--cov` reports
despite being genuinely exercised by those suites. These tests call `ingest_document`/
`delete_knowledge`/`_run_on_demand_ingestion` directly from the test's own coroutine
(no `TestClient` involved) so coverage attributes hits correctly, while still
asserting the same real behavior against the live test Postgres instance (mirrors
`tests/unit/test_repositories.py`'s `NullPool` pattern).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import normalize_asyncpg_url
from app.errors import IdempotencyKeyConflictError, InvalidRequestError, KnowledgeNotFoundError
from app.graphs.nodes import embed_chunks as embed_chunks_module
from app.models.ingestion_job import IngestionJob
from app.models.knowledge_document import KnowledgeDocument
from app.models.knowledge_source import KnowledgeSource
from app.repositories.ingestion_job_repository import IngestionJobRepository
from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from app.repositories.knowledge_source_repository import KnowledgeSourceRepository
from app.services import ingestion_service
from app.utils.metrics import knowledge_documents_deleted_total


@pytest_asyncio.fixture
async def session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Own throwaway `NullPool` engine; `ingestion_service`'s own `AsyncSessionLocal`
    binding (used by `_run_on_demand_ingestion`'s background-task body) is rebound to
    it too, so the background task opens a connection against the same test database
    rather than the real app's pooled engine."""
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ingestion_service, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


class _StubBedrockClient:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        self.calls += 1
        return [[0.01] * 1024 for _ in texts]


@pytest.fixture
def stub_bedrock(monkeypatch: pytest.MonkeyPatch) -> _StubBedrockClient:
    stub = _StubBedrockClient()
    monkeypatch.setattr(embed_chunks_module, "bedrock_client", stub)
    return stub


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _cleanup_document(
    session_factory: async_sessionmaker[AsyncSession], document_id: uuid.UUID
) -> None:
    async with session_factory() as session:
        repo = KnowledgeDocumentRepository(session)
        document = await repo.get_by_id(document_id)
        if document is not None:
            await repo.delete(document)
            await session.commit()


# --- Idempotency-Key handling (lines 62-72) ------------------------------------


async def test_ingest_document_idempotency_retry_returns_original_result(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    key = _unique("idem")
    text = "Konten idempotency-key yang pertama dan tidak berubah."

    async with session_factory() as session:
        first_id, first_status = await ingestion_service.ingest_document(
            session,
            BackgroundTasks(),
            source_type="text",
            raw_bytes=None,
            text=text,
            title=_unique("idem-doc"),
            valid_until=None,
            supersedes_document_id=None,
            idempotency_key=key,
        )
        assert first_status == "queued"

    try:
        async with session_factory() as session:
            second_id, second_status = await ingestion_service.ingest_document(
                session,
                BackgroundTasks(),
                source_type="text",
                raw_bytes=None,
                text=text,
                title=_unique("idem-doc-retry"),
                valid_until=None,
                supersedes_document_id=None,
                idempotency_key=key,
            )

        # Same key, same content: returns the ORIGINAL document/status, no new row.
        assert second_id == first_id
        assert second_status == "queued"
    finally:
        await _cleanup_document(session_factory, first_id)


async def test_ingest_document_idempotency_conflict_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    key = _unique("idem-conflict")

    async with session_factory() as session:
        first_id, _ = await ingestion_service.ingest_document(
            session,
            BackgroundTasks(),
            source_type="text",
            raw_bytes=None,
            text="Konten pertama untuk uji konflik idempotency-key.",
            title=_unique("idem-conflict-doc"),
            valid_until=None,
            supersedes_document_id=None,
            idempotency_key=key,
        )

    try:
        async with session_factory() as session:
            with pytest.raises(IdempotencyKeyConflictError):
                await ingestion_service.ingest_document(
                    session,
                    BackgroundTasks(),
                    source_type="text",
                    raw_bytes=None,
                    text="Konten KEDUA yang sepenuhnya berbeda.",
                    title=_unique("idem-conflict-doc-2"),
                    valid_until=None,
                    supersedes_document_id=None,
                    idempotency_key=key,
                )
    finally:
        await _cleanup_document(session_factory, first_id)


# --- supersedes_document_id validation (lines 75-81) ---------------------------


async def test_ingest_document_unknown_supersedes_id_raises_invalid_request(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        with pytest.raises(InvalidRequestError):
            await ingestion_service.ingest_document(
                session,
                BackgroundTasks(),
                source_type="text",
                raw_bytes=None,
                text="teks apa saja",
                title=None,
                valid_until=None,
                supersedes_document_id=uuid.uuid4(),
                idempotency_key=None,
            )


# --- Document creation + background-task scheduling (lines 83-113) -------------


async def test_ingest_document_wires_supersedes_and_runs_background_pipeline(
    session_factory: async_sessionmaker[AsyncSession],
    stub_bedrock: _StubBedrockClient,
) -> None:
    async with session_factory() as session:
        old_document = await KnowledgeDocumentRepository(session).create(
            KnowledgeDocument(source_type="text", title=_unique("old-doc"), status="completed")
        )
        await session.commit()
        old_id = old_document.id

    background_tasks = BackgroundTasks()
    async with session_factory() as session:
        new_id, status = await ingestion_service.ingest_document(
            session,
            background_tasks,
            source_type="text",
            raw_bytes=None,
            text="Konten dokumen baru yang menggantikan dokumen lama. " * 5,
            title=_unique("new-doc"),
            valid_until=None,
            supersedes_document_id=old_id,
            idempotency_key=None,
        )
        assert status == "queued"

    # Runs the scheduled `_run_on_demand_ingestion` synchronously, in this same
    # coroutine/thread (mirrors how Starlette's own BackgroundTasks execution works,
    # just without TestClient's separate-thread portal).
    await background_tasks()

    try:
        async with session_factory() as session:
            old_refreshed = await KnowledgeDocumentRepository(session).get_by_id(old_id)
            assert old_refreshed is not None
            assert old_refreshed.superseded_by_document_id == new_id

            new_refreshed = await KnowledgeDocumentRepository(session).get_by_id(new_id)
            assert new_refreshed is not None
            assert new_refreshed.status == "completed"
            assert new_refreshed.chunk_count > 0

            jobs = (
                (
                    await session.execute(
                        select(IngestionJob).where(IngestionJob.document_id == new_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(jobs) == 1
            assert jobs[0].status == "completed"
    finally:
        await _cleanup_document(session_factory, old_id)
        await _cleanup_document(session_factory, new_id)


# --- _run_on_demand_ingestion: job vanished (lines 131-133) ---------------------


async def test_run_on_demand_ingestion_missing_job_logs_and_returns(
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("ERROR"):
        await ingestion_service._run_on_demand_ingestion(
            document_id=uuid.uuid4(),
            job_id=uuid.uuid4(),
            source_type="text",
            source_ref="irrelevant",
            raw_bytes=None,
        )
    assert any("not found" in record.message for record in caplog.records)


# --- _run_on_demand_ingestion: pipeline crash (lines 152-169) -------------------


async def test_run_on_demand_ingestion_crash_marks_document_and_job_failed(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        document = await KnowledgeDocumentRepository(session).create(
            KnowledgeDocument(source_type="text", title=_unique("crash-doc"), status="queued")
        )
        job = await IngestionJobRepository(session).create(
            IngestionJob(job_type="on_demand", document_id=document.id, status="queued")
        )
        await session.commit()
        document_id, job_id = document.id, job.id

    class _ExplodingGraph:
        async def ainvoke(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("simulated ingestion graph crash")

    # Replaces the module-global name `ingestion_graph` used by `_run_on_demand_ingestion`
    # (not the shared singleton object itself) so this test can't affect other tests.
    monkeypatch.setattr(ingestion_service, "ingestion_graph", _ExplodingGraph())

    try:
        await ingestion_service._run_on_demand_ingestion(
            document_id=document_id,
            job_id=job_id,
            source_type="text",
            source_ref="teks apa saja",
            raw_bytes=None,
        )

        async with session_factory() as session:
            failed_document = await KnowledgeDocumentRepository(session).get_by_id(document_id)
            assert failed_document is not None
            assert failed_document.status == "failed"
            assert failed_document.error_message == "simulated ingestion graph crash"

            failed_job = await IngestionJobRepository(session).get_by_id(job_id)
            assert failed_job is not None
            assert failed_job.status == "failed"
            assert failed_job.error_message == "simulated ingestion graph crash"
    finally:
        await _cleanup_document(session_factory, document_id)


# --- delete_knowledge (lines 189-220) -------------------------------------------


async def test_delete_knowledge_unknown_id_raises_not_found(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        with pytest.raises(KnowledgeNotFoundError):
            await ingestion_service.delete_knowledge(session, knowledge_id=uuid.uuid4())


async def test_delete_knowledge_removes_document_counts_chunks_and_increments_metric(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = await KnowledgeDocumentRepository(session).create(
            KnowledgeDocument(source_type="text", title=_unique("delete-doc"), status="completed")
        )
        await session.flush()
        embedding = [0.0] * 1024
        from app.models.knowledge_chunk import KnowledgeChunk

        await KnowledgeChunkRepository(session).bulk_create(
            [
                KnowledgeChunk(
                    document_id=document.id, content="chunk 0", chunk_index=0, embedding=embedding
                ),
                KnowledgeChunk(
                    document_id=document.id, content="chunk 1", chunk_index=1, embedding=embedding
                ),
            ]
        )
        await session.commit()
        document_id = document.id

    metric_before = knowledge_documents_deleted_total._value.get()

    async with session_factory() as session:
        chunks_removed = await ingestion_service.delete_knowledge(session, knowledge_id=document_id)

    assert chunks_removed == 2
    assert knowledge_documents_deleted_total._value.get() == metric_before + 1

    async with session_factory() as session:
        assert await KnowledgeDocumentRepository(session).get_by_id(document_id) is None


async def test_delete_knowledge_resets_source_ingested_flag(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        source = await KnowledgeSourceRepository(session).create(
            KnowledgeSource(
                relative_path=_unique("docs/delete-reset"), is_ingested=True, content_hash="abc"
            )
        )
        document = await KnowledgeDocumentRepository(session).create(
            KnowledgeDocument(
                source_id=source.id,
                source_type="url",
                title=_unique("source-linked-doc"),
                status="completed",
            )
        )
        await session.commit()
        source_id, document_id = source.id, document.id

    try:
        async with session_factory() as session:
            await ingestion_service.delete_knowledge(session, knowledge_id=document_id)

        async with session_factory() as session:
            refreshed_source = await KnowledgeSourceRepository(session).get_by_id(source_id)
            assert refreshed_source is not None
            assert refreshed_source.is_ingested is False
    finally:
        async with session_factory() as session:
            repo = KnowledgeSourceRepository(session)
            row = await repo.get_by_id(source_id)
            if row is not None:
                await repo.delete(row)
                await session.commit()
