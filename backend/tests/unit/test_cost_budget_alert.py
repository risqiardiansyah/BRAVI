"""Unit tests for `app.services.cost_budget_service.run_cost_budget_check` —
docs/19-cost-management.md §4, docs/IMPLEMENTATION_PLAN.md Phase 13 task 4.
Definition of Done: "the cost alert fires exactly at threshold in a seeded test, not
before or after."

Mirrors `tests/unit/test_retention_job.py`'s `AsyncSessionLocal`-rebinding pattern.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import normalize_asyncpg_url
from app.models.usage_metric import UsageMetric
from app.repositories.usage_metric_repository import UsageMetricRepository
from app.services import cost_budget_service
from app.utils.metrics import daily_cost_budget_exceeded


@pytest_asyncio.fixture
async def app_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(cost_budget_service, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


async def _seed_today_cost(
    factory: async_sessionmaker[AsyncSession], *, cost_usd: float
) -> UsageMetric:
    async with factory() as session:
        row = await UsageMetricRepository(session).create(
            UsageMetric(
                endpoint="/api/chat",
                created_at=datetime.now(UTC),
                estimated_cost_usd=cost_usd,
            )
        )
        await session.commit()
        return row


async def _cleanup(factory: async_sessionmaker[AsyncSession], row: UsageMetric) -> None:
    async with factory() as session:
        existing = await UsageMetricRepository(session).get_by_id(row.id)
        if existing is not None:
            await UsageMetricRepository(session).delete(existing)
            await session.commit()


async def test_no_alert_when_budget_unset(
    app_session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DAILY_COST_BUDGET_USD", None)
    row = await _seed_today_cost(app_session_factory, cost_usd=999.99)
    try:
        result = await cost_budget_service.run_cost_budget_check()
        assert result.exceeded is False
        assert result.budget_usd is None
        assert daily_cost_budget_exceeded._value.get() == 0
    finally:
        await _cleanup(app_session_factory, row)


async def test_alert_fires_exactly_at_threshold_not_before(
    app_session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DAILY_COST_BUDGET_USD", 10.0)
    row = await _seed_today_cost(app_session_factory, cost_usd=9.99)
    try:
        result = await cost_budget_service.run_cost_budget_check()
        assert result.exceeded is False
        assert daily_cost_budget_exceeded._value.get() == 0
    finally:
        await _cleanup(app_session_factory, row)


async def test_alert_fires_at_exact_threshold(
    app_session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DAILY_COST_BUDGET_USD", 10.0)
    row = await _seed_today_cost(app_session_factory, cost_usd=10.0)
    try:
        result = await cost_budget_service.run_cost_budget_check()
        assert result.exceeded is True
        assert daily_cost_budget_exceeded._value.get() == 1
    finally:
        await _cleanup(app_session_factory, row)


async def test_alert_fires_above_threshold(
    app_session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DAILY_COST_BUDGET_USD", 10.0)
    row = await _seed_today_cost(app_session_factory, cost_usd=10.01)
    try:
        result = await cost_budget_service.run_cost_budget_check()
        assert result.exceeded is True
        assert daily_cost_budget_exceeded._value.get() == 1
    finally:
        await _cleanup(app_session_factory, row)


async def test_yesterdays_cost_is_not_counted_toward_todays_budget(
    app_session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DAILY_COST_BUDGET_USD", 10.0)
    async with app_session_factory() as session:
        row = await UsageMetricRepository(session).create(
            UsageMetric(
                endpoint="/api/chat",
                created_at=datetime.now(UTC) - timedelta(days=1),
                estimated_cost_usd=1_000.0,
            )
        )
        await session.commit()
    try:
        result = await cost_budget_service.run_cost_budget_check()
        assert result.exceeded is False
        assert result.total_cost_usd == 0.0
    finally:
        await _cleanup(app_session_factory, row)
