"""Integration tests for `POST /api/opr/ingest` — docs/06-api-specification.md §6,
docs/22-error-handling.md §4, docs/IMPLEMENTATION_PLAN.md Phase 7.

Bedrock is mocked (module-level `bedrock_client` rebound in
`app.graphs.nodes.embed_chunks`, mirroring `tests/integration/test_ingestion_graph.py`);
the database is the same live test Postgres+pgvector instance every other integration
test suite uses. `TestClient` runs each request's `BackgroundTasks` synchronously as
part of the call itself, so by the time `client.post(...)` returns, the on-demand
ingestion pipeline has already finished — no polling/sleep needed to observe the
final `status`/`chunk_count` via `GET /api/opr/knowledge`.

Rate limiting (Phase 4) is bypassed here via a no-op `rate_limiter.enforce` — it has
its own dedicated test suite (`tests/unit/test_rate_limit.py`,
`tests/integration/test_rate_limit_multi_instance.py`) and re-exercising it against
real Redis here would only risk flaky failures from the shared token bucket across
repeated test runs, not add coverage.

Each `TestClient` call drives the ASGI app through its own blocking portal/event loop;
the app's real `AsyncSessionLocal` (`app/db.py`) is backed by a pooled engine
(`pool_size=20`), so a connection checked out under one call's loop can be handed back
out under a *different* call's loop later, and asyncpg raises "another operation is in
progress" the moment that reused connection is awaited from the new loop. Every test
here rebinds both `app.db.AsyncSessionLocal` (read by `get_session`) and
`app.services.ingestion_service.AsyncSessionLocal` (its own `from app.db import ...`
binding — patching `app.db`'s attribute alone would not reach it) to a throwaway
`NullPool` engine, so every checkout is a brand-new physical connection regardless of
which loop asks — mirrors `tests/integration/test_startup_ingestion_idempotency.py`'s
`session_factory` fixture.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import db as db_module
from app.config import settings
from app.graphs.nodes import embed_chunks as embed_chunks_module
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.services import ingestion_service as ingestion_service_module
from tests.pdf_fixtures import build_minimal_pdf


@pytest_asyncio.fixture
async def app_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    assert settings.DATABASE_URL
    engine = create_async_engine(
        db_module.normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool
    )
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


@pytest.fixture
def cleanup_ids(
    app_session_factory: async_sessionmaker[AsyncSession],
) -> Generator[list[str], None, None]:
    """Collects `knowledge_id`s created during a test; deletes them via the
    (already under test) DELETE endpoint at teardown, so runs don't accumulate
    documents in the shared dev database. Depends on `app_session_factory` explicitly
    so its `NullPool` engine outlives this fixture's own teardown (pytest tears down
    fixtures in reverse dependency order)."""
    ids: list[str] = []
    yield ids
    cleanup_client = TestClient(app)
    for knowledge_id in ids:
        cleanup_client.delete(f"/api/opr/knowledge/{knowledge_id}")


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _find(knowledge_list: list[dict[str, object]], knowledge_id: str) -> dict[str, object]:
    for item in knowledge_list:
        if item["id"] == knowledge_id:
            return item
    raise AssertionError(f"{knowledge_id} not found in GET /api/opr/knowledge listing")


# --- Happy paths ---------------------------------------------------------------


def test_text_ingestion_happy_path(
    client: TestClient, stub_bedrock: _StubBedrockClient, cleanup_ids: list[str]
) -> None:
    title = _unique("text-ingest")
    response = client.post(
        "/api/opr/ingest",
        data={
            "text": "Ini adalah teks pengujian ingestion melalui endpoint operator.",
            "title": title,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    knowledge_id = body["knowledge_id"]
    cleanup_ids.append(knowledge_id)

    listing = client.get("/api/opr/knowledge").json()
    item = _find(listing["knowledge"], knowledge_id)
    assert item["status"] == "completed"
    assert item["chunk_count"] > 0
    assert item["source_type"] == "text"
    assert item["url"] is None


def test_file_ingestion_happy_path(
    client: TestClient, stub_bedrock: _StubBedrockClient, cleanup_ids: list[str]
) -> None:
    pdf_bytes = build_minimal_pdf(["Konten pengujian file PDF untuk endpoint ingest operator."])
    title = _unique("file-ingest")
    response = client.post(
        "/api/opr/ingest",
        files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
        data={"title": title},
    )

    assert response.status_code == 202
    knowledge_id = response.json()["knowledge_id"]
    cleanup_ids.append(knowledge_id)

    listing = client.get("/api/opr/knowledge").json()
    item = _find(listing["knowledge"], knowledge_id)
    assert item["status"] == "completed"
    assert item["chunk_count"] > 0
    assert item["source_type"] == "file"


# --- Request validation ----------------------------------------------------------


def test_missing_file_and_text_returns_400(client: TestClient) -> None:
    response = client.post("/api/opr/ingest", data={"title": "no content at all"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_both_file_and_text_returns_400(client: TestClient) -> None:
    pdf_bytes = build_minimal_pdf(["x"])
    response = client.post(
        "/api/opr/ingest",
        files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
        data={"text": "also text"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_supersedes_unknown_document_id_returns_400(client: TestClient) -> None:
    response = client.post(
        "/api/opr/ingest",
        data={"text": "teks apa saja", "supersedes_document_id": str(uuid.uuid4())},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_invalid_valid_until_returns_400(client: TestClient) -> None:
    response = client.post(
        "/api/opr/ingest",
        data={"text": "teks apa saja", "valid_until": "not-a-date"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


# --- Idempotency-Key (docs/22-error-handling.md §4) -----------------------------


def test_idempotency_key_retry_with_same_content_returns_original_result(
    client: TestClient, stub_bedrock: _StubBedrockClient, cleanup_ids: list[str]
) -> None:
    key = _unique("idem")
    title = _unique("idem-doc")
    payload = {"text": "Konten idempotency-key yang pertama dan tidak berubah.", "title": title}

    first = client.post("/api/opr/ingest", data=payload, headers={"Idempotency-Key": key})
    assert first.status_code == 202
    first_id = first.json()["knowledge_id"]
    cleanup_ids.append(first_id)
    calls_after_first = stub_bedrock.calls
    assert calls_after_first > 0

    second = client.post("/api/opr/ingest", data=payload, headers={"Idempotency-Key": key})
    assert second.status_code == 202
    second_body = second.json()
    assert second_body["knowledge_id"] == first_id
    assert second_body["status"] == "completed"  # the ORIGINAL request's current status
    assert stub_bedrock.calls == calls_after_first  # no duplicate embedding job started

    listing = client.get("/api/opr/knowledge").json()
    matches = [item for item in listing["knowledge"] if item["title"] == title]
    assert len(matches) == 1  # no duplicate knowledge_documents row


def test_idempotency_key_conflict_with_different_content_returns_409(
    client: TestClient, stub_bedrock: _StubBedrockClient, cleanup_ids: list[str]
) -> None:
    key = _unique("idem-conflict")
    title = _unique("idem-conflict-doc")

    first = client.post(
        "/api/opr/ingest",
        data={"text": "Konten pertama untuk uji konflik idempotency-key.", "title": title},
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 202
    first_id = first.json()["knowledge_id"]
    cleanup_ids.append(first_id)

    second = client.post(
        "/api/opr/ingest",
        data={
            "text": "Konten KEDUA yang sepenuhnya berbeda untuk uji konflik ini.",
            "title": title,
        },
        headers={"Idempotency-Key": key},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"

    listing = client.get("/api/opr/knowledge").json()
    matches = [item for item in listing["knowledge"] if item["title"] == title]
    assert len(matches) == 1  # the conflicting request never created a second document


# --- valid_until / supersedes_document_id (docs/07-database-design.md §5b) ------


def test_valid_until_and_supersedes_document_id_are_wired(
    client: TestClient, stub_bedrock: _StubBedrockClient, cleanup_ids: list[str]
) -> None:
    old_title = _unique("superseded-doc")
    old = client.post(
        "/api/opr/ingest",
        data={"text": "Dokumen lama yang nantinya akan digantikan.", "title": old_title},
    )
    assert old.status_code == 202
    old_id = old.json()["knowledge_id"]
    cleanup_ids.append(old_id)

    new_title = _unique("superseding-doc")
    new = client.post(
        "/api/opr/ingest",
        data={
            "text": "Dokumen baru yang menggantikan dokumen lama di atas sepenuhnya.",
            "title": new_title,
            "valid_until": "2026-12-31",
            "supersedes_document_id": old_id,
        },
    )
    assert new.status_code == 202
    new_id = new.json()["knowledge_id"]
    cleanup_ids.append(new_id)

    listing = client.get("/api/opr/knowledge").json()
    old_item = _find(listing["knowledge"], old_id)
    new_item = _find(listing["knowledge"], new_id)

    # Set on the OLD document, pointing at the new one — not the other way around.
    assert old_item["superseded_by_document_id"] == new_id
    assert new_item["superseded_by_document_id"] is None
    assert new_item["valid_until"] == "2026-12-31"
