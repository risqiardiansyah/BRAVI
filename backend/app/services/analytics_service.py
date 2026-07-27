"""Trending questions + Operator analytics aggregation —
docs/06-api-specification.md §4/§8, docs/07-database-design.md §4,
docs/02-functional-requirements.md FR-4/FR-9.

Reads directly from `usage_metrics`, populated by both chat graphs' `log_chat_metrics`
node since Phase 9/10 — no new graph/node work, only aggregation over existing data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.usage_metric_repository import UsageMetricRepository
from app.schemas.analytics import (
    AnalyticsPeriod,
    AnalyticsResponse,
    Latency,
    ModelUsage,
    TopQuestions,
    TrendingQuestionItem,
    Volume,
    VolumeByDayItem,
)

TRENDING_DEFAULT_WINDOW_DAYS = 7
TRENDING_DEFAULT_LIMIT = 10
ANALYTICS_DEFAULT_WINDOW_DAYS = 30


async def get_trending(
    session: AsyncSession, *, window_days: int, limit: int
) -> tuple[int, list[TrendingQuestionItem]]:
    """`GET /api/trending` — docs/06-api-specification.md §4. Aggregates
    `usage_metrics.question` across both personas over a rolling `window_days` window
    (FR-4 defines no persona restriction; the endpoint itself is User-facing)."""
    since = datetime.now(UTC) - timedelta(days=window_days)
    rows = await UsageMetricRepository(session).top_questions(since=since, limit=limit)
    return window_days, [
        TrendingQuestionItem(question=question, count=count) for question, count in rows
    ]


async def get_operator_analytics(
    session: AsyncSession, *, date_from: date | None, date_to: date | None
) -> AnalyticsResponse:
    """`GET /api/opr/analytics` — docs/06-api-specification.md §8, FR-9."""
    resolved_to = date_to or datetime.now(UTC).date()
    resolved_from = date_from or (resolved_to - timedelta(days=ANALYTICS_DEFAULT_WINDOW_DAYS))

    since = datetime.combine(resolved_from, time.min, tzinfo=UTC)
    until = datetime.combine(resolved_to, time.max, tzinfo=UTC)

    repo = UsageMetricRepository(session)

    # FR-9: "top_questions.user — non-role calculation, counts questions from both User
    # and Operator sessions together, not split by persona" — `persona=None` below.
    top_question_rows = await repo.top_questions(since=since, limit=10, persona=None)
    top_questions = [
        TrendingQuestionItem(question=question, count=count)
        for question, count in top_question_rows
    ]

    total_chats = await repo.total_chats(since=since, until=until)
    by_day_rows = await repo.volume_by_day(since=since, until=until)
    by_day = [VolumeByDayItem(date=day, count=count) for day, count in by_day_rows]

    p50_ms, p95_ms = await repo.latency_percentiles(since=since, until=until)

    embedding_calls, text_generation_calls, total_rows = await repo.model_usage(
        since=since, until=until
    )
    short_circuited = await repo.short_circuited_count(since=since, until=until)
    short_circuited_pct = (short_circuited / total_rows * 100) if total_rows else 0.0

    estimated_cost_usd = await repo.total_estimated_cost(since=since, until=until)

    return AnalyticsResponse(
        period=AnalyticsPeriod.model_validate({"from": resolved_from, "to": resolved_to}),
        top_questions=TopQuestions(user=top_questions),
        volume=Volume(total_chats=total_chats, by_day=by_day),
        latency=Latency(p50_ms=p50_ms, p95_ms=p95_ms),
        model_usage=ModelUsage(
            embedding_calls=embedding_calls,
            text_generation_calls=text_generation_calls,
            short_circuited_pct=round(short_circuited_pct, 1),
        ),
        estimated_cost_usd=estimated_cost_usd,
    )
