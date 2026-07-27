"""Integration tests for `GET /api/trending` — docs/06-api-specification.md §4,
docs/02-functional-requirements.md FR-4, docs/IMPLEMENTATION_PLAN.md Phase 11.

`AsyncSessionLocal` is rebound to a throwaway `NullPool` engine for the same reason
established in `tests/integration/test_session_endpoints.py` (Phase 8) — a `TestClient`
call drives the app through its own event loop, and the app's real pooled engine can
hand back a connection checked out under a different call's loop, which asyncpg rejects.
Seeded `usage_metrics` rows are deleted afterward (no retention/cleanup endpoint exists
yet — Phase 13 adds that).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import db as db_module
from app.config import settings
from app.db import normalize_asyncpg_url
from app.main import app
from app.models.usage_metric import UsageMetric
from app.repositories.usage_metric_repository import UsageMetricRepository


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


async def _seed(
    factory: async_sessionmaker[AsyncSession],
    *,
    question: str,
    created_at: datetime,
    persona: str = "user",
) -> int:
    async with factory() as session:
        row = await UsageMetricRepository(session).create(
            UsageMetric(
                persona=persona,
                endpoint="/api/chat",
                question=question,
                short_circuited=False,
                created_at=created_at,
            )
        )
        await session.commit()
        return row.id


async def _delete(factory: async_sessionmaker[AsyncSession], ids: list[int]) -> None:
    async with factory() as session:
        repo = UsageMetricRepository(session)
        for row_id in ids:
            row = await repo.get_by_id(row_id)
            if row is not None:
                await repo.delete(row)
        await session.commit()


async def test_trending_counts_and_orders_by_frequency_within_window(
    client: TestClient, app_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    now = datetime.now(UTC)
    marker = now.timestamp()
    q_popular = f"apa itu refund {marker}"
    q_rare = f"bagaimana cara bayar {marker}"
    q_stale = f"pertanyaan lama {marker}"

    ids: list[int] = []
    ids.append(await _seed(app_session_factory, question=q_popular, created_at=now))
    ids.append(
        await _seed(app_session_factory, question=q_popular.upper(), created_at=now)
    )  # normalized (lowercased) into the same bucket as q_popular
    ids.append(await _seed(app_session_factory, question=q_rare, created_at=now))
    # Outside the 7-day window entirely - must not be counted.
    ids.append(
        await _seed(app_session_factory, question=q_stale, created_at=now - timedelta(days=30))
    )

    try:
        response = client.get("/api/trending", params={"limit": 10, "window_days": 7})
        assert response.status_code == 200
        body = response.json()
        assert body["window_days"] == 7
        questions = {item["question"]: item["count"] for item in body["trending"]}
        assert questions[q_popular.lower()] == 2
        assert questions[q_rare.lower()] == 1
        assert q_stale.lower() not in questions
    finally:
        await _delete(app_session_factory, ids)


async def test_trending_respects_limit(
    client: TestClient, app_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    now = datetime.now(UTC)
    marker = now.timestamp()
    ids = [
        await _seed(app_session_factory, question=f"unique question {marker} {i}", created_at=now)
        for i in range(3)
    ]

    try:
        response = client.get("/api/trending", params={"limit": 1, "window_days": 7})
        assert response.status_code == 200
        assert len(response.json()["trending"]) == 1
    finally:
        await _delete(app_session_factory, ids)


def test_trending_rejects_non_positive_limit(client: TestClient) -> None:
    response = client.get("/api/trending", params={"limit": 0})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_trending_rejects_non_positive_window_days(client: TestClient) -> None:
    response = client.get("/api/trending", params={"window_days": -1})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_trending_defaults_match_documented_example(client: TestClient) -> None:
    response = client.get("/api/trending")
    assert response.status_code == 200
    assert response.json()["window_days"] == 7
