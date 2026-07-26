"""Shared declarative base for all ORM models — docs/07-database-design.md."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
