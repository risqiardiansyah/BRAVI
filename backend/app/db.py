"""Async SQLAlchemy engine/session factory — docs/11-coding-standard.md §7.

Repositories receive an `AsyncSession` (e.g. via `get_session` as a FastAPI
dependency); they do not own engine lifecycle themselves.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


def normalize_asyncpg_url(url: str) -> str:
    """Ensure the asyncpg driver is selected regardless of the scheme documented
    in docs/10-deployment.md §3 (`postgresql://...`, no explicit driver)."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _build_engine() -> AsyncEngine:
    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured; cannot create the database engine.")
    return create_async_engine(
        normalize_asyncpg_url(settings.DATABASE_URL),
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        connect_args={
            "server_settings": {"statement_timeout": str(settings.DB_STATEMENT_TIMEOUT_MS)}
        },
    )


engine: AsyncEngine = _build_engine()
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped `AsyncSession`, closing it on completion."""
    async with AsyncSessionLocal() as session:
        yield session
