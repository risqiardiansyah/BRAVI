"""Integration tests for `chat_service.resolve_session` — docs/06-api-specification.md
§2/§5 (the session resolution rule shared by `/api/chat`/`/api/opr/chat`),
docs/07-database-design.md §3.1, docs/IMPLEMENTATION_PLAN.md Phase 8.

Exercises `resolve_session` directly against the same live test Postgres instance
every other integration suite uses — the chat endpoints that will call it don't exist
until Phase 9/10 (this phase's own manual-verification note). Mirrors
`tests/unit/test_repositories.py`'s `db_session` fixture: a throwaway `NullPool`
engine on this test's own event loop, rolled back (never committed) at teardown, so
nothing persists across test runs.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import normalize_asyncpg_url
from app.errors import SessionNotFoundError
from app.services import chat_service


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    assert settings.DATABASE_URL
    engine = create_async_engine(normalize_asyncpg_url(settings.DATABASE_URL), poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.mark.parametrize("persona", ["user", "operator"])
async def test_empty_session_id_auto_creates_a_new_session(
    db_session: AsyncSession, persona: str
) -> None:
    user_id = _unique("user")
    session = await chat_service.resolve_session(
        db_session, session_id=None, user_id=user_id, persona=persona
    )
    assert session.session_id is not None
    assert session.user_id == user_id
    assert session.persona == persona
    assert session.title is None


@pytest.mark.parametrize("persona", ["user", "operator"])
async def test_valid_existing_session_id_is_reused(db_session: AsyncSession, persona: str) -> None:
    user_id = _unique("user")
    created = await chat_service.resolve_session(
        db_session, session_id=None, user_id=user_id, persona=persona
    )

    reused = await chat_service.resolve_session(
        db_session, session_id=created.session_id, user_id=user_id, persona=persona
    )
    assert reused.session_id == created.session_id
    assert reused.user_id == user_id
    assert reused.persona == persona


@pytest.mark.parametrize("persona", ["user", "operator"])
async def test_unknown_session_id_raises_session_not_found(
    db_session: AsyncSession, persona: str
) -> None:
    with pytest.raises(SessionNotFoundError):
        await chat_service.resolve_session(
            db_session, session_id=uuid.uuid4(), user_id=_unique("user"), persona=persona
        )
