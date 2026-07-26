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
    created = await repo.create(UsageMetric(endpoint="/api/chat"))

    fetched = await repo.get_by_id(created.id)
    assert fetched is not None
    assert fetched.short_circuited is False

    await repo.delete(fetched)
    assert await repo.get_by_id(created.id) is None
