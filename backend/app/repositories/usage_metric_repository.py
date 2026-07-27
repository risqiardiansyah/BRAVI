"""Repository for `usage_metrics` — docs/07-database-design.md §3.7/§4.

Aggregation queries for `GET /api/trending`/`GET /api/opr/analytics` (Phase 11) live
here rather than in `services/analytics_service.py`, per `11-coding-standard.md` §4
("repositories are the only layer executing SQL/ORM queries").
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Row, delete, func, select

from app.models.usage_metric import UsageMetric
from app.repositories.base import BaseRepository


class UsageMetricRepository(BaseRepository[UsageMetric]):
    model = UsageMetric

    async def delete_older_than(self, cutoff: datetime) -> int:
        """Purges `usage_metrics` rows older than `cutoff` —
        `services/retention_service.py` (docs/07-database-design.md §7)."""
        result = cast(
            CursorResult[Any],
            await self._session.execute(delete(UsageMetric).where(UsageMetric.created_at < cutoff)),
        )
        return result.rowcount or 0

    async def top_questions(
        self, *, since: datetime, limit: int, persona: str | None = None
    ) -> list[Row[tuple[str, int]]]:
        """Top-N normalized questions (lowercased/trimmed, docs/07-database-design.md
        §4) asked since `since`, most-frequent first. `persona=None` counts every row
        regardless of persona (`06-api-specification.md` §8's `top_questions.user` is a
        non-role calculation per `02-functional-requirements.md` FR-9)."""
        normalized = func.lower(func.trim(UsageMetric.question)).label("question")
        stmt = (
            select(normalized, func.count().label("count"))
            .where(UsageMetric.created_at >= since, UsageMetric.question.isnot(None))
            .group_by(normalized)
            .order_by(func.count().desc())
            .limit(limit)
        )
        if persona is not None:
            stmt = stmt.where(UsageMetric.persona == persona)
        result = await self._session.execute(stmt)
        return list(result.all())

    async def volume_by_day(
        self, *, since: datetime, until: datetime
    ) -> list[Row[tuple[date, int]]]:
        """Chat-turn count per calendar day within `[since, until]`
        (docs/06-api-specification.md §8's `volume.by_day`)."""
        day = func.date(UsageMetric.created_at).label("day")
        stmt = (
            select(day, func.count().label("count"))
            .where(UsageMetric.created_at >= since, UsageMetric.created_at <= until)
            .group_by(day)
            .order_by(day)
        )
        result = await self._session.execute(stmt)
        return list(result.all())

    async def total_chats(self, *, since: datetime, until: datetime) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(UsageMetric)
            .where(UsageMetric.created_at >= since, UsageMetric.created_at <= until)
        )
        return result.scalar_one()

    async def latency_percentiles(
        self, *, since: datetime, until: datetime
    ) -> tuple[float | None, float | None]:
        """`p50_ms`/`p95_ms` via `percentile_cont`, per docs/07-database-design.md §4."""
        p50 = func.percentile_cont(0.5).within_group(UsageMetric.latency_ms.asc())
        p95 = func.percentile_cont(0.95).within_group(UsageMetric.latency_ms.asc())
        result = await self._session.execute(
            select(p50, p95).where(
                UsageMetric.created_at >= since,
                UsageMetric.created_at <= until,
                UsageMetric.latency_ms.isnot(None),
            )
        )
        row = result.one()
        return row[0], row[1]

    async def model_usage(self, *, since: datetime, until: datetime) -> tuple[int, int, int]:
        """`(embedding_calls, text_generation_calls, total_rows)` for the
        `model_usage` block — `embedding_calls`/`text_generation_calls` count rows
        where the respective model field was actually populated (i.e. that call
        genuinely happened; short-circuited tiers leave it `NULL`)."""
        result = await self._session.execute(
            select(
                func.count(UsageMetric.model_embedding_used),
                func.count(UsageMetric.model_text_used),
                func.count(),
            ).where(UsageMetric.created_at >= since, UsageMetric.created_at <= until)
        )
        row = result.one()
        return row[0], row[1], row[2]

    async def short_circuited_count(self, *, since: datetime, until: datetime) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(UsageMetric)
            .where(
                UsageMetric.created_at >= since,
                UsageMetric.created_at <= until,
                UsageMetric.short_circuited.is_(True),
            )
        )
        return result.scalar_one()

    async def total_estimated_cost(self, *, since: datetime, until: datetime) -> float:
        result = await self._session.execute(
            select(func.coalesce(func.sum(UsageMetric.estimated_cost_usd), 0)).where(
                UsageMetric.created_at >= since, UsageMetric.created_at <= until
            )
        )
        value = result.scalar_one()
        return float(value) if value is not None else 0.0
