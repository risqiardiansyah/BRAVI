"""Direct unit tests for `app.services.analytics_service` —
docs/06-api-specification.md §4/§8, docs/07-database-design.md §4,
docs/IMPLEMENTATION_PLAN.md Phase 14.

`tests/integration/test_analytics.py`/`test_trending.py` already exercise
`get_operator_analytics`/`get_trending` functionally via `TestClient`, but several of
`get_operator_analytics`'s own statements (everything past the `top_questions`
aggregation) still show up as uncovered in `--cov` reports — `TestClient` runs the
ASGI app on its own dedicated event-loop thread whenever called from an `async def`
test, and `coverage.py` does not trace new threads unless `concurrency = thread` is
configured (it isn't, here). Calling `get_operator_analytics` directly sidesteps that
gap while asserting the same aggregation-correctness contract against seeded
`usage_metrics` fixtures (`12-testing-strategy.md` §3).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, time, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import normalize_asyncpg_url
from app.models.usage_metric import UsageMetric
from app.repositories.usage_metric_repository import UsageMetricRepository
from app.services import analytics_service


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


async def test_get_operator_analytics_aggregates_seeded_fixtures(
    db_session: AsyncSession,
) -> None:
    marker = datetime.now(UTC).timestamp()
    question = f"refund policy {marker}"
    greeting_question = f"halo {marker}"

    day1 = date.today() - timedelta(days=1)
    day2 = date.today()
    day1_dt = datetime.combine(day1, time(10, 0), tzinfo=UTC)
    day2_dt = datetime.combine(day2, time(10, 0), tzinfo=UTC)

    repo = UsageMetricRepository(db_session)
    await repo.create(
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
        )
    )
    await repo.create(
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
        )
    )
    await repo.create(
        UsageMetric(
            persona="user",
            endpoint="/api/chat",
            question=greeting_question,
            short_circuited=True,
            short_circuit_reason="greeting",
            latency_ms=10,
            created_at=day2_dt,
        )
    )
    await db_session.flush()

    response = await analytics_service.get_operator_analytics(
        db_session, date_from=day1, date_to=day2
    )

    assert response.period.from_ == day1
    assert response.period.to == day2

    top_questions = {item.question: item.count for item in response.top_questions.user}
    assert top_questions[question.lower()] == 2  # combined across both personas
    assert top_questions[greeting_question.lower()] == 1

    by_day = {item.date: item.count for item in response.volume.by_day}
    assert by_day[day1] == 2
    assert by_day[day2] == 1
    assert response.volume.total_chats == 3

    assert response.latency.p50_ms == pytest.approx(100.0)
    assert response.latency.p95_ms == pytest.approx(190.0)

    assert response.model_usage.embedding_calls == 2
    assert response.model_usage.text_generation_calls == 2
    assert response.model_usage.short_circuited_pct == pytest.approx(33.3, abs=0.1)

    assert response.estimated_cost_usd == pytest.approx(3.0)


async def test_get_operator_analytics_defaults_window_when_dates_omitted(
    db_session: AsyncSession,
) -> None:
    response = await analytics_service.get_operator_analytics(
        db_session, date_from=None, date_to=None
    )
    today = datetime.now(UTC).date()
    assert response.period.to == today
    assert response.period.from_ == today - timedelta(
        days=analytics_service.ANALYTICS_DEFAULT_WINDOW_DAYS
    )


async def test_get_operator_analytics_zero_rows_yields_zero_short_circuited_pct(
    db_session: AsyncSession,
) -> None:
    """No `usage_metrics` rows in a far-future window — `total_rows` is 0, so the
    `short_circuited_pct` division-by-zero guard (`if total_rows else 0.0`) must
    return `0.0` rather than raising."""
    future_day = date.today() + timedelta(days=3650)
    response = await analytics_service.get_operator_analytics(
        db_session, date_from=future_day, date_to=future_day
    )
    assert response.model_usage.short_circuited_pct == 0.0
    assert response.volume.total_chats == 0
    assert response.estimated_cost_usd == 0.0


async def test_get_trending_direct_call_aggregates_within_window(
    db_session: AsyncSession,
) -> None:
    marker = datetime.now(UTC).timestamp()
    question = f"cara klaim asuransi {marker}"
    now = datetime.now(UTC)

    repo = UsageMetricRepository(db_session)
    await repo.create(
        UsageMetric(
            persona="user",
            endpoint="/api/chat",
            question=question,
            short_circuited=False,
            created_at=now,
        )
    )
    await repo.create(
        UsageMetric(
            persona="user",
            endpoint="/api/chat",
            question=question.upper(),
            short_circuited=False,
            created_at=now,
        )
    )
    await db_session.flush()

    window_days, items = await analytics_service.get_trending(db_session, window_days=7, limit=10)
    assert window_days == 7
    matched = next(item for item in items if item.question == question.lower())
    assert matched.count == 2
