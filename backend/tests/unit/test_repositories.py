"""Basic CRUD smoke tests for every repository — docs/07-database-design.md §3.

Requires a live Postgres+pgvector database reachable via `DATABASE_URL`
with migrations applied (docs/IMPLEMENTATION_PLAN.md Phase 2 Verification:
"pytest tests/unit/test_repositories.py ... against a test database").
Each test runs inside a transaction that is rolled back afterward, so no
data persists across runs.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, time, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import normalize_asyncpg_url
from app.models.ingestion_job import IngestionJob
from app.models.knowledge_chunk import EMBEDDING_DIMENSION, KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.models.knowledge_source import KnowledgeSource
from app.models.message import Message
from app.models.session import Session as SessionModel
from app.models.usage_metric import UsageMetric
from app.repositories.ingestion_job_repository import IngestionJobRepository
from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from app.repositories.knowledge_source_repository import KnowledgeSourceRepository
from app.repositories.message_repository import MessageRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.usage_metric_repository import UsageMetricRepository


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """A session on a throwaway, `NullPool` engine created within this test's
    own event loop — pytest-asyncio gives each test its own loop, and asyncpg
    connections cannot be reused across loops, so the module-level engine in
    `app.db` (a long-lived singleton) must not be shared across tests here."""
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def test_session_repository_crud(db_session: AsyncSession) -> None:
    repo = SessionRepository(db_session)
    created = await repo.create(SessionModel(user_id=_unique("user"), persona="user"))

    fetched = await repo.get_by_id(created.session_id)
    assert fetched is not None
    assert fetched.persona == "user"

    all_sessions = await repo.list_all()
    assert any(s.session_id == created.session_id for s in all_sessions)

    await repo.delete(fetched)
    assert await repo.get_by_id(created.session_id) is None


async def test_message_repository_crud(db_session: AsyncSession) -> None:
    session_repo = SessionRepository(db_session)
    session = await session_repo.create(SessionModel(user_id=_unique("user"), persona="user"))

    repo = MessageRepository(db_session)
    created = await repo.create(
        Message(session_id=session.session_id, role="user", content="hello")
    )

    fetched = await repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.content == "hello"
    assert fetched.has_image is False

    await repo.delete(fetched)
    assert await repo.get_by_id(created.id) is None


async def test_message_repository_delete_older_than(db_session: AsyncSession) -> None:
    """docs/07-database-design.md §7 — `services/retention_service.py` purges
    `messages` older than a cutoff, leaving newer rows (and the `sessions` row itself)
    untouched."""
    session_repo = SessionRepository(db_session)
    session = await session_repo.create(SessionModel(user_id=_unique("user"), persona="user"))

    now = datetime.now(UTC)
    repo = MessageRepository(db_session)
    old = await repo.create(
        Message(
            session_id=session.session_id,
            role="user",
            content="old",
            created_at=now - timedelta(days=100),
        )
    )
    recent = await repo.create(
        Message(
            session_id=session.session_id,
            role="user",
            content="recent",
            created_at=now - timedelta(days=1),
        )
    )
    await db_session.flush()

    cutoff = now - timedelta(days=90)
    deleted_count = await repo.delete_older_than(cutoff)

    # >=1 rather than ==1: this shared dev database may already hold other old rows
    # from prior test/dev activity — this test only asserts about its own two rows.
    assert deleted_count >= 1
    assert await repo.get_by_id(old.id) is None
    assert await repo.get_by_id(recent.id) is not None
    assert await session_repo.get_by_id(session.session_id) is not None


async def test_knowledge_source_repository_crud(db_session: AsyncSession) -> None:
    repo = KnowledgeSourceRepository(db_session)
    created = await repo.create(KnowledgeSource(relative_path=_unique("docs/path")))

    fetched = await repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.is_ingested is False

    await repo.delete(fetched)
    assert await repo.get_by_id(created.id) is None


async def test_knowledge_document_repository_crud(db_session: AsyncSession) -> None:
    repo = KnowledgeDocumentRepository(db_session)
    created = await repo.create(KnowledgeDocument(source_type="text"))

    fetched = await repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.status == "queued"
    assert fetched.chunk_count == 0

    await repo.delete(fetched)
    assert await repo.get_by_id(created.id) is None


async def test_knowledge_chunk_repository_crud(db_session: AsyncSession) -> None:
    document_repo = KnowledgeDocumentRepository(db_session)
    document = await document_repo.create(KnowledgeDocument(source_type="text"))

    repo = KnowledgeChunkRepository(db_session)
    embedding = [0.0] * EMBEDDING_DIMENSION
    created = await repo.create(
        KnowledgeChunk(
            document_id=document.id,
            content="chunk text",
            chunk_index=0,
            embedding=embedding,
        )
    )

    fetched = await repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.embedding is not None
    assert len(fetched.embedding) == EMBEDDING_DIMENSION

    await repo.delete(fetched)
    assert await repo.get_by_id(created.id) is None


async def test_ingestion_job_repository_crud(db_session: AsyncSession) -> None:
    repo = IngestionJobRepository(db_session)
    created = await repo.create(IngestionJob(job_type="on_demand"))

    fetched = await repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.status == "queued"

    await repo.delete(fetched)
    assert await repo.get_by_id(created.id) is None


async def test_usage_metric_repository_crud(db_session: AsyncSession) -> None:
    repo = UsageMetricRepository(db_session)
    created = await repo.create(UsageMetric(endpoint="/api/chat", ttft_ms=420))

    fetched = await repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.short_circuited is False
    assert fetched.ttft_ms == 420

    await repo.delete(fetched)
    assert await repo.get_by_id(created.id) is None


async def test_usage_metric_repository_delete_older_than(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    repo = UsageMetricRepository(db_session)
    old = await repo.create(UsageMetric(endpoint="/api/chat", created_at=now - timedelta(days=200)))
    recent = await repo.create(
        UsageMetric(endpoint="/api/chat", created_at=now - timedelta(days=1))
    )
    await db_session.flush()

    cutoff = now - timedelta(days=180)
    deleted_count = await repo.delete_older_than(cutoff)

    # >=1 rather than ==1: this shared dev database may already hold other old rows
    # from prior test/dev activity — this test only asserts about its own two rows.
    assert deleted_count >= 1
    assert await repo.get_by_id(old.id) is None
    assert await repo.get_by_id(recent.id) is not None


async def test_session_repository_list_by_user_id_paginates_newest_first(
    db_session: AsyncSession,
) -> None:
    """docs/06-api-specification.md §1 — `GET /api/session`'s `list_by_user_id`:
    newest-first ordering, `limit`/`offset` applied, plus the total matching count
    (unaffected by pagination)."""
    repo = SessionRepository(db_session)
    user_id = _unique("user")
    other_user_id = _unique("other-user")
    now = datetime.now(UTC)

    # Explicit, distinct `created_at` values: Postgres's `now()` (this table's
    # `server_default`) returns the SAME value for every statement within one
    # transaction, so relying on insertion order alone would make the "newest-first"
    # assertion below flaky.
    older = await repo.create(
        SessionModel(user_id=user_id, persona="user", created_at=now - timedelta(minutes=1))
    )
    newer = await repo.create(SessionModel(user_id=user_id, persona="operator", created_at=now))
    # A different user's session must never appear in this user's page/total.
    await repo.create(SessionModel(user_id=other_user_id, persona="user", created_at=now))
    await db_session.flush()

    sessions, total = await repo.list_by_user_id(user_id, limit=10, offset=0)
    assert total == 2
    assert [s.session_id for s in sessions] == [newer.session_id, older.session_id]

    first_page, total_first = await repo.list_by_user_id(user_id, limit=1, offset=0)
    assert total_first == 2
    assert [s.session_id for s in first_page] == [newer.session_id]

    second_page, total_second = await repo.list_by_user_id(user_id, limit=1, offset=1)
    assert total_second == 2
    assert [s.session_id for s in second_page] == [older.session_id]


async def test_usage_metric_repository_top_questions_filters_by_persona(
    db_session: AsyncSession,
) -> None:
    """docs/07-database-design.md §4 — `top_questions(persona=...)` restricts the
    aggregation to one persona's rows only (used nowhere in the current services, but
    part of the repository's documented public contract)."""
    repo = UsageMetricRepository(db_session)
    since = datetime.now(UTC) - timedelta(minutes=5)
    marker = datetime.now(UTC).timestamp()
    question = f"persona filter question {marker}"

    await repo.create(
        UsageMetric(persona="user", endpoint="/api/chat", question=question, short_circuited=False)
    )
    await repo.create(
        UsageMetric(
            persona="operator", endpoint="/api/opr/chat", question=question, short_circuited=False
        )
    )
    await db_session.flush()

    user_only = await repo.top_questions(since=since, limit=10, persona="user")
    assert dict(user_only)[question.lower()] == 1

    both_personas = await repo.top_questions(since=since, limit=10, persona=None)
    assert dict(both_personas)[question.lower()] == 2


async def test_usage_metric_repository_aggregation_helpers(db_session: AsyncSession) -> None:
    """Direct coverage for `volume_by_day`/`total_chats`/`latency_percentiles`/
    `model_usage`/`short_circuited_count` — `services/analytics_service.py`'s own
    aggregation building blocks (docs/06-api-specification.md §8)."""
    repo = UsageMetricRepository(db_session)
    marker = datetime.now(UTC).timestamp()
    day = datetime.combine(date.today(), time(9, 0), tzinfo=UTC)
    since = day - timedelta(hours=1)
    until = day + timedelta(hours=1)

    await repo.create(
        UsageMetric(
            persona="user",
            endpoint="/api/chat",
            question=f"aggregation helper {marker}",
            short_circuited=False,
            model_embedding_used="cohere.embed-v4",
            model_text_used="claude",
            latency_ms=50,
            estimated_cost_usd=0.5,
            created_at=day,
        )
    )
    await repo.create(
        UsageMetric(
            persona="user",
            endpoint="/api/chat",
            question=f"aggregation helper short-circuit {marker}",
            short_circuited=True,
            short_circuit_reason="greeting",
            latency_ms=5,
            created_at=day,
        )
    )
    await db_session.flush()

    by_day_rows = await repo.volume_by_day(since=since, until=until)
    by_day = dict(by_day_rows)
    assert by_day[date.today()] >= 2

    total = await repo.total_chats(since=since, until=until)
    assert total >= 2

    p50, p95 = await repo.latency_percentiles(since=since, until=until)
    assert p50 is not None
    assert p95 is not None

    embedding_calls, text_generation_calls, total_rows = await repo.model_usage(
        since=since, until=until
    )
    assert embedding_calls >= 1
    assert text_generation_calls >= 1
    assert total_rows >= 2

    short_circuited = await repo.short_circuited_count(since=since, until=until)
    assert short_circuited >= 1

    total_cost = await repo.total_estimated_cost(since=since, until=until)
    assert total_cost >= 0.5
