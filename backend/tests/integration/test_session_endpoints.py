"""Integration tests for `GET /api/session`/`POST /api/messages` —
docs/06-api-specification.md §1/§3, docs/IMPLEMENTATION_PLAN.md Phase 8.

Not the two files explicitly named in Phase 8's own Verification checklist (which
targets `chat_service.resolve_session`/`persist_message` directly, since no chat
endpoint calls them until Phase 9/10) — these two endpoints are this phase's other
Task-list deliverable (`api/user_router.py`), so they get their own direct HTTP-level
coverage here.

`AsyncSessionLocal` is rebound to a throwaway `NullPool` engine for the same reason
established in `tests/integration/test_ingest_endpoint.py` (Phase 7): a `TestClient`
call drives the ASGI app through its own event loop, and the app's real pooled engine
can hand back a connection checked out under a different call's loop, which asyncpg
rejects. Rows created directly through `app_session_factory` are committed (visible to
the router's own session) and explicitly deleted afterward — `sessions`/`messages`
have no delete endpoint yet (Phase 13 adds retention-based cleanup only), so nothing
else would remove them.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import db as db_module
from app.config import settings
from app.db import normalize_asyncpg_url
from app.main import app
from app.repositories.session_repository import SessionRepository
from app.services import chat_service


@pytest_asyncio.fixture
async def app_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


@pytest.fixture
def client(app_session_factory: async_sessionmaker[AsyncSession]) -> TestClient:
    return TestClient(app)


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _delete_session(
    app_session_factory: async_sessionmaker[AsyncSession], session_id: uuid.UUID
) -> None:
    async with app_session_factory() as session:
        repo = SessionRepository(session)
        row = await repo.get_by_id(session_id)
        if row is not None:
            await repo.delete(row)  # ON DELETE CASCADE removes its messages too
            await session.commit()


def test_list_sessions_missing_user_id_returns_400(client: TestClient) -> None:
    response = client.get("/api/session")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_list_sessions_blank_user_id_returns_400(client: TestClient) -> None:
    response = client.get("/api/session", params={"user_id": "   "})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


async def test_list_sessions_returns_sessions_with_title(
    client: TestClient, app_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    user_id = _unique("user")
    async with app_session_factory() as session:
        created = await chat_service.resolve_session(
            session, session_id=None, user_id=user_id, persona="user"
        )
        await chat_service.persist_message(
            session, session_id=created.session_id, role="user", content="Halo dunia"
        )
        await session.commit()
        session_id = created.session_id

    try:
        response = client.get("/api/session", params={"user_id": user_id})
        assert response.status_code == 200
        body = response.json()
        assert body["user_id"] == user_id
        assert body["total"] == 1
        assert len(body["sessions"]) == 1
        item = body["sessions"][0]
        assert item["session_id"] == str(session_id)
        assert item["persona"] == "user"
        assert item["title"] == "Halo dunia"
    finally:
        await _delete_session(app_session_factory, session_id)


def test_get_messages_unknown_session_returns_404(client: TestClient) -> None:
    response = client.post("/api/messages", json={"session_id": str(uuid.uuid4())})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"


async def test_get_messages_returns_ordered_history(
    client: TestClient, app_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with app_session_factory() as session:
        created = await chat_service.resolve_session(
            session, session_id=None, user_id=_unique("user"), persona="user"
        )
        await chat_service.persist_message(
            session, session_id=created.session_id, role="user", content="Pertanyaan"
        )
        await chat_service.persist_message(
            session, session_id=created.session_id, role="assistant", content="Jawaban"
        )
        await session.commit()
        session_id = created.session_id

    try:
        response = client.post("/api/messages", json={"session_id": str(session_id)})
        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == str(session_id)
        assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
        assert [m["content"] for m in body["messages"]] == ["Pertanyaan", "Jawaban"]
    finally:
        await _delete_session(app_session_factory, session_id)


def test_get_messages_missing_session_id_returns_400(client: TestClient) -> None:
    response = client.post("/api/messages", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
