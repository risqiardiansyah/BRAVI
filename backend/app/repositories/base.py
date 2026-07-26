"""Generic async CRUD shared by every table-specific repository.

Repositories are the only layer executing SQL/ORM queries
(docs/11-coding-standard.md §4). Business-specific query methods belong in
each subclass, added by the phase that first needs them — this base only
covers the CRUD operations every repository needs regardless of table.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, instance: ModelT) -> ModelT:
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def get_by_id(self, id_: Any) -> ModelT | None:
        return await self._session.get(self.model, id_)

    async def list_all(self) -> list[ModelT]:
        result = await self._session.execute(select(self.model))
        return list(result.scalars().all())

    async def delete(self, instance: ModelT) -> None:
        await self._session.delete(instance)
        await self._session.flush()
