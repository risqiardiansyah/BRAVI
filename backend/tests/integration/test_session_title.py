"""Integration tests for `chat_service.persist_message`'s title set-once behavior —
docs/07-database-design.md §3.1 ("set once, the first time a `role='user'` message is
persisted for that session ... never overwritten afterward"), docs/IMPLEMENTATION_PLAN.md
Phase 8.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import normalize_asyncpg_url
from app.repositories.session_repository import SessionRepository
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


async def test_title_set_from_first_user_message_truncated_to_60_chars(
    db_session: AsyncSession,
) -> None:
    created = await chat_service.resolve_session(
        db_session, session_id=None, user_id=_unique("user"), persona="user"
    )
    long_question = "Apa itu " + ("kebijakan pengembalian produk " * 5)
    assert len(long_question) > 60

    await chat_service.persist_message(
        db_session, session_id=created.session_id, role="user", content=long_question
    )

    refreshed = await SessionRepository(db_session).get_by_id(created.session_id)
    assert refreshed is not None
    assert refreshed.title == long_question.strip()[:60]
    assert len(refreshed.title) == 60


async def test_title_never_overwritten_by_later_user_messages(db_session: AsyncSession) -> None:
    created = await chat_service.resolve_session(
        db_session, session_id=None, user_id=_unique("user"), persona="user"
    )

    await chat_service.persist_message(
        db_session, session_id=created.session_id, role="user", content="Pertanyaan pertama."
    )
    after_first = await SessionRepository(db_session).get_by_id(created.session_id)
    assert after_first is not None
    assert after_first.title == "Pertanyaan pertama."

    await chat_service.persist_message(
        db_session, session_id=created.session_id, role="assistant", content="Jawaban pertama."
    )
    await chat_service.persist_message(
        db_session,
        session_id=created.session_id,
        role="user",
        content="Pertanyaan kedua yang sama sekali berbeda.",
    )

    after_second = await SessionRepository(db_session).get_by_id(created.session_id)
    assert after_second is not None
    assert after_second.title == "Pertanyaan pertama."  # unchanged by later messages


async def test_short_title_is_stripped_but_not_padded(db_session: AsyncSession) -> None:
    created = await chat_service.resolve_session(
        db_session, session_id=None, user_id=_unique("user"), persona="user"
    )
    await chat_service.persist_message(
        db_session, session_id=created.session_id, role="user", content="  Halo  "
    )
    refreshed = await SessionRepository(db_session).get_by_id(created.session_id)
    assert refreshed is not None
    assert refreshed.title == "Halo"
