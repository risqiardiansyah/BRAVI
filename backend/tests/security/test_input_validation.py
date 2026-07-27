"""Input validation boundary tests — docs/08-security.md §3, docs/12-testing-strategy.md
§5 ("oversized payloads, invalid MIME types, path traversal attempts in ingestion
`relative_path`"), docs/IMPLEMENTATION_PLAN.md Phase 12.

`ChatRequestFields`' validators (question/user_id) are exercised directly — pure,
DB-free unit tests. The ingest endpoint's text-length/file-MIME checks and the startup
job's path-traversal guard are exercised at the boundary they actually live at.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import db as db_module
from app.config import settings
from app.jobs.run_initial_ingestion import _build_source_url
from app.main import app
from app.middleware import rate_limit as rate_limit_module
from app.schemas.chat import ChatRequestFields
from app.services import ingestion_service as ingestion_service_module

# --- `ChatRequestFields` — pure unit tests, no DB/app needed ---------------------------


def test_question_over_max_length_rejected() -> None:
    with pytest.raises(ValidationError):
        ChatRequestFields(question="a" * 2_001, user_id="test-user")


def test_question_at_max_length_accepted() -> None:
    fields = ChatRequestFields(question="a" * 2_000, user_id="test-user")
    assert len(fields.question) == 2_000


def test_question_control_characters_stripped() -> None:
    fields = ChatRequestFields(question="halo\x00\x01dunia", user_id="test-user")
    assert fields.question == "halodunia"


def test_question_preserves_newlines_and_tabs() -> None:
    fields = ChatRequestFields(question="halo\ndunia\tapa kabar", user_id="test-user")
    assert fields.question == "halo\ndunia\tapa kabar"


def test_user_id_over_max_length_rejected() -> None:
    with pytest.raises(ValidationError):
        ChatRequestFields(question="halo", user_id="a" * 129)


def test_user_id_disallowed_charset_rejected() -> None:
    with pytest.raises(ValidationError):
        ChatRequestFields(question="halo", user_id="<script>alert(1)</script>")


def test_user_id_allowed_charset_accepted() -> None:
    fields = ChatRequestFields(question="halo", user_id="test.user-01@example.com")
    assert fields.user_id == "test.user-01@example.com"


# --- Ingestion `relative_path` — path traversal (docs/08-security.md §2 SSRF row) ------


def test_relative_path_traversal_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_BASE_URL", "https://example.com/documents")
    with pytest.raises(ValueError, match="path traversal"):
        _build_source_url("../../etc/passwd")


def test_relative_path_normal_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_BASE_URL", "https://example.com/documents")
    url = _build_source_url("sample.pdf")
    assert url == "https://example.com/documents/sample.pdf"


# --- `/api/opr/ingest` boundary tests — oversized `text`, invalid `file` MIME ----------


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


@pytest.fixture(autouse=True)
def _bypass_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*, endpoint: str, identity: str) -> None:
        return None

    monkeypatch.setattr(rate_limit_module.rate_limiter, "enforce", _noop)


def test_ingest_oversized_text_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/opr/ingest",
        data={"text": "a" * 200_001, "title": "oversized-text-test"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_ingest_file_wrong_mime_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/opr/ingest",
        files={"file": ("payload.exe", b"MZ\x90\x00", "application/x-msdownload")},
        data={"title": "wrong-mime-test"},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_ingest_file_oversized_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MAX_FILE_UPLOAD_MB", 0)
    response = client.post(
        "/api/opr/ingest",
        files={"file": ("sample.pdf", b"%PDF-1.4\n...", "application/pdf")},
        data={"title": "oversized-file-test"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_chat_image_oversized_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MAX_IMAGE_UPLOAD_MB", 0)
    response = client.post(
        "/api/chat",
        files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n...", "image/png")},
        data={"question": "Halo", "user_id": "test-user"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_chat_image_wrong_mime_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/chat",
        files={"file": ("payload.exe", b"MZ\x90\x00", "application/x-msdownload")},
        data={"question": "Halo", "user_id": "test-user"},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
