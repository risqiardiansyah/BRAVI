"""Integration tests for `GET /api/opr/analytics` — docs/06-api-specification.md §8,
docs/07-database-design.md §4, docs/02-functional-requirements.md FR-9,
docs/IMPLEMENTATION_PLAN.md Phase 11.

Same `NullPool`-rebound-engine rationale as `tests/integration/test_trending.py`.
Aggregation is verified against a small, fully-controlled `usage_metrics` fixture set
(`12-testing-strategy.md` §3: "aggregation correctness against seeded fixtures").
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, time, timedelta

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


async def _seed(factory: async_sessionmaker[AsyncSession], row: UsageMetric) -> int:
    async with factory() as session:
        created = await UsageMetricRepository(session).create(row)
        await session.commit()
        return created.id


async def _delete(factory: async_sessionmaker[AsyncSession], ids: list[int]) -> None:
    async with factory() as session:
        repo = UsageMetricRepository(session)
        for row_id in ids:
            row = await repo.get_by_id(row_id)
            if row is not None:
                await repo.delete(row)
        await session.commit()


async def test_analytics_aggregates_seeded_fixtures(
    client: TestClient, app_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    marker = datetime.now(UTC).timestamp()
    question = f"refund policy {marker}"
    greeting_question = f"halo {marker}"

    day1 = date.today() - timedelta(days=1)
    day2 = date.today()
    day1_dt = datetime.combine(day1, time(10, 0), tzinfo=UTC)
    day2_dt = datetime.combine(day2, time(10, 0), tzinfo=UTC)

    ids: list[int] = []
    # A genuine RAG answer, asked once by a User and once by an Operator, on day1.
    ids.append(
        await _seed(
            app_session_factory,
            UsageMetric(
                persona="user",
                endpoint="/api/chat",
                question=question,
                short_circuited=False,
                model_embedding_used="cohere.embed-v4",
                model_text_used="claude",
                latency_ms=100,
                estimated_cost_usd=1.0,
                created_at=day1_dt,
            ),
        )
    )
    ids.append(
        await _seed(
            app_session_factory,
            UsageMetric(
                persona="operator",
                endpoint="/api/opr/chat",
                question=question,
                short_circuited=False,
                model_embedding_used="cohere.embed-v4",
                model_text_used="claude",
                latency_ms=200,
                estimated_cost_usd=2.0,
                created_at=day1_dt,
            ),
        )
    )
    # A short-circuited greeting on day2 - no embedding/text-generation call, no cost.
    ids.append(
        await _seed(
            app_session_factory,
            UsageMetric(
                persona="user",
                endpoint="/api/chat",
                question=greeting_question,
                short_circuited=True,
                short_circuit_reason="greeting",
                latency_ms=10,
                created_at=day2_dt,
            ),
        )
    )

    try:
        response = client.get("/api/opr/analytics", params={"from": str(day1), "to": str(day2)})
        assert response.status_code == 200
        body = response.json()

        assert body["period"] == {"from": str(day1), "to": str(day2)}

        top_questions = {item["question"]: item["count"] for item in body["top_questions"]["user"]}
        assert top_questions[question.lower()] == 2  # combined across both personas
        assert top_questions[greeting_question.lower()] == 1

        by_day = {item["date"]: item["count"] for item in body["volume"]["by_day"]}
        assert by_day[str(day1)] == 2
        assert by_day[str(day2)] == 1
        assert body["volume"]["total_chats"] == 3

        assert body["latency"]["p50_ms"] == pytest.approx(100.0)
        assert body["latency"]["p95_ms"] == pytest.approx(190.0)

        assert body["model_usage"]["embedding_calls"] == 2
        assert body["model_usage"]["text_generation_calls"] == 2
        assert body["model_usage"]["short_circuited_pct"] == pytest.approx(33.3, abs=0.1)

        assert body["estimated_cost_usd"] == pytest.approx(3.0)
    finally:
        await _delete(app_session_factory, ids)


def test_analytics_rejects_from_after_to(client: TestClient) -> None:
    response = client.get("/api/opr/analytics", params={"from": "2026-07-20", "to": "2026-07-01"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_analytics_defaults_to_a_rolling_window_when_no_params_given(
    client: TestClient,
) -> None:
    response = client.get("/api/opr/analytics")
    assert response.status_code == 200
    body = response.json()
    assert body["period"]["to"] == str(datetime.now(UTC).date())
