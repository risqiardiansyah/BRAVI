"""Unit tests for `app.services.retention_service.run_retention_cleanup` —
docs/07-database-design.md §7, docs/03-non-functional-requirements.md §8,
docs/IMPLEMENTATION_PLAN.md Phase 13.

`AsyncSessionLocal` is rebound to a throwaway `NullPool` engine, swapped in for the
job module's own `AsyncSessionLocal` reference (`retention_service.py` does `from
app.db import AsyncSessionLocal`, binding its own module-level name — patching
`app.db.AsyncSessionLocal` itself would not affect it) — mirroring
`tests/integration/test_startup_ingestion_idempotency.py`'s established pattern for
the same reason: a pytest-asyncio test has its own event loop that asyncpg
connections can't be shared across, so the module-level singleton engine in `app.db`
must not be used here. Rows are created directly and any surviving ones are
explicitly deleted afterward.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import normalize_asyncpg_url
from app.models.message import Message
from app.models.session import Session as SessionModel
from app.models.usage_metric import UsageMetric
from app.repositories.message_repository import MessageRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.usage_metric_repository import UsageMetricRepository
from app.services import retention_service


@pytest_asyncio.fixture
async def app_session_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(retention_service, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def test_run_retention_cleanup_purges_only_rows_older_than_the_configured_windows(
    app_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MESSAGE_RETENTION_DAYS", 90)
    monkeypatch.setattr(settings, "USAGE_METRICS_RETENTION_DAYS", 180)

    now = datetime.now(UTC)
    async with app_session_factory() as session:
        session_row = await SessionRepository(session).create(
            SessionModel(user_id=_unique("user"), persona="user")
        )
        old_message = await MessageRepository(session).create(
            Message(
                session_id=session_row.session_id,
                role="user",
                content="old",
                created_at=now - timedelta(days=91),
            )
        )
        recent_message = await MessageRepository(session).create(
            Message(
                session_id=session_row.session_id,
                role="user",
                content="recent",
                created_at=now - timedelta(days=1),
            )
        )
        old_usage_metric = await UsageMetricRepository(session).create(
            UsageMetric(endpoint="/api/chat", created_at=now - timedelta(days=181))
        )
        recent_usage_metric = await UsageMetricRepository(session).create(
            UsageMetric(endpoint="/api/chat", created_at=now - timedelta(days=1))
        )
        await session.commit()

    try:
        result = await retention_service.run_retention_cleanup()

        assert result.messages_deleted >= 1
        assert result.usage_metrics_deleted >= 1

        async with app_session_factory() as session:
            assert await MessageRepository(session).get_by_id(old_message.id) is None
            assert await MessageRepository(session).get_by_id(recent_message.id) is not None
            assert await UsageMetricRepository(session).get_by_id(old_usage_metric.id) is None
            assert (
                await UsageMetricRepository(session).get_by_id(recent_usage_metric.id) is not None
            )
            # sessions rows are left in place — only messages are pruned.
            assert await SessionRepository(session).get_by_id(session_row.session_id) is not None
    finally:
        async with app_session_factory() as session:
            recent = await MessageRepository(session).get_by_id(recent_message.id)
            if recent is not None:
                await MessageRepository(session).delete(recent)
            recent_metric = await UsageMetricRepository(session).get_by_id(recent_usage_metric.id)
            if recent_metric is not None:
                await UsageMetricRepository(session).delete(recent_metric)
            session_to_delete = await SessionRepository(session).get_by_id(session_row.session_id)
            if session_to_delete is not None:
                await SessionRepository(session).delete(session_to_delete)
            await session.commit()
