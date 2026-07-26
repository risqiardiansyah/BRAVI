"""Integration tests for `DELETE /api/opr/knowledge/{id}` —
docs/06-api-specification.md §7.1, docs/07-database-design.md §5a,
docs/IMPLEMENTATION_PLAN.md Phase 7.

`ingestion_jobs.document_id -> NULL` and `knowledge_sources.is_ingested` reset aren't
observable through any endpoint (no GET exposes either), so these tests query the
tables directly via `app_session_factory` — a throwaway `NullPool` engine/sessionmaker.
Each logical step (seed, verify-before, verify-after) opens its own fresh session
rather than reusing one across the `DELETE` call: reusing a single session across a
`commit()`/external-mutation boundary hit both SQLAlchemy identity-map staleness (a
previously loaded row isn't automatically re-fetched) and a `MissingGreenlet` error
when the session needed to open a brand-new connection mid-test. Opening a new session
per step (mirroring `tests/integration/test_startup_ingestion_idempotency.py`) sidesteps
both. Writes are committed (not rolled back) so the app's own session (used by the
`TestClient` requests below) can see them — a different connection to the same real
Postgres instance, not a different database.

Each `TestClient` call also drives the ASGI app through its own blocking
portal/event loop; the app's real `AsyncSessionLocal` (`app/db.py`) is backed by a
pooled engine (`pool_size=20`), so a connection checked out under one call's loop can
be handed back out under a *different* call's loop later, and asyncpg raises "another
operation is in progress" the moment that reused connection is awaited from the new
loop. `app_session_factory` rebinds both `app.db.AsyncSessionLocal` (read by
`get_session`) and `app.services.ingestion_service.AsyncSessionLocal` (its own
`from app.db import ...` binding) to its own throwaway `NullPool` engine, so every
checkout is a brand-new physical connection regardless of which loop asks.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import db as db_module
from app.config import settings
from app.db import normalize_asyncpg_url
from app.graphs.nodes import embed_chunks as embed_chunks_module
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.models.ingestion_job import IngestionJob
from app.models.knowledge_document import KnowledgeDocument
from app.models.knowledge_source import KnowledgeSource
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from app.repositories.knowledge_source_repository import KnowledgeSourceRepository
from app.services import ingestion_service as ingestion_service_module


@pytest_asyncio.fixture
async def app_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(ingestion_service_module, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


@pytest.fixture
def client(app_session_factory: async_sessionmaker[AsyncSession]) -> TestClient:
    return TestClient(app)


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


@pytest.fixture(autouse=True)
def _bypass_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*, endpoint: str, identity: str) -> None:
        return None

    monkeypatch.setattr(rate_limit_module.rate_limiter, "enforce", _noop)


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def test_delete_cascades_chunks_and_preserves_job_history(
    client: TestClient,
    stub_bedrock: _StubBedrockClient,
    app_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    title = _unique("delete-cascade")
    ingest_response = client.post(
        "/api/opr/ingest",
        data={"text": "Dokumen yang akan dihapus sepenuhnya beserta chunk-nya.", "title": title},
    )
    assert ingest_response.status_code == 202
    knowledge_id = ingest_response.json()["knowledge_id"]
    document_uuid = uuid.UUID(knowledge_id)

    listing = client.get("/api/opr/knowledge").json()
    item = next(i for i in listing["knowledge"] if i["id"] == knowledge_id)
    assert item["status"] == "completed"
    assert item["chunk_count"] > 0

    async with app_session_factory() as session:
        jobs_before = (
            (
                await session.execute(
                    select(IngestionJob).where(IngestionJob.document_id == document_uuid)
                )
            )
            .scalars()
            .all()
        )
        assert len(jobs_before) == 1
        job_id = jobs_before[0].id

    delete_response = client.delete(f"/api/opr/knowledge/{knowledge_id}")
    assert delete_response.status_code == 200
    delete_body = delete_response.json()
    assert delete_body["knowledge_id"] == knowledge_id
    assert delete_body["status"] == "deleted"
    assert delete_body["chunks_removed"] == item["chunk_count"]

    async with app_session_factory() as session:
        # Document (and, via DB-level ON DELETE CASCADE, its knowledge_chunks) is gone.
        document = await KnowledgeDocumentRepository(session).get_by_id(document_uuid)
        assert document is None

        # ingestion_jobs row preserved for audit history, document_id -> NULL (SET NULL FK).
        job = await session.get(IngestionJob, job_id)
        assert job is not None
        assert job.document_id is None

    # Already-deleted id -> 404, not a crash/duplicate-delete side effect.
    second_delete = client.delete(f"/api/opr/knowledge/{knowledge_id}")
    assert second_delete.status_code == 404
    assert second_delete.json()["error"]["code"] == "KNOWLEDGE_NOT_FOUND"


def test_delete_unknown_id_returns_404(client: TestClient) -> None:
    response = client.delete(f"/api/opr/knowledge/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "KNOWLEDGE_NOT_FOUND"


async def test_delete_resets_is_ingested_for_source_linked_document(
    client: TestClient, app_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A startup-managed document (has `source_id`) — deleting it must reset
    `knowledge_sources.is_ingested` to `false` (docs/07-database-design.md §5a): the
    *next* startup ingestion run will re-ingest it unless the source row is also
    removed, a documented trade-off rather than a bug."""
    relative_path = _unique("docs/delete-reset-test") + ".pdf"
    async with app_session_factory() as session:
        source = await KnowledgeSourceRepository(session).create(
            KnowledgeSource(relative_path=relative_path, is_ingested=True, content_hash="deadbeef")
        )
        document = await KnowledgeDocumentRepository(session).create(
            KnowledgeDocument(
                source_id=source.id,
                title="Source-linked doc for delete-reset test",
                source_type="url",
                status="completed",
            )
        )
        await session.commit()
        source_id, document_id = source.id, document.id

    delete_response = client.delete(f"/api/opr/knowledge/{document_id}")
    assert delete_response.status_code == 200

    async with app_session_factory() as session:
        refreshed_source = await KnowledgeSourceRepository(session).get_by_id(source_id)
        assert refreshed_source is not None
        assert refreshed_source.is_ingested is False

        # Cleanup: this test's own directly-seeded source row (the document itself
        # was already removed by the DELETE call under test).
        await session.delete(refreshed_source)
        await session.commit()
