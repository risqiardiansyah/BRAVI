"""SQLAlchemy ORM models — one module per table, docs/07-database-design.md §3.

Every model must be imported here so `Base.metadata` is fully populated for
Alembic's `target_metadata` (see migrations/env.py) and for `alembic check`.
"""

from app.models.base import Base
from app.models.ingestion_job import IngestionJob
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.knowledge_document import KnowledgeDocument
from app.models.knowledge_source import KnowledgeSource
from app.models.message import Message
from app.models.session import Session
from app.models.usage_metric import UsageMetric

__all__ = [
    "Base",
    "IngestionJob",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "KnowledgeSource",
    "Message",
    "Session",
    "UsageMetric",
]
