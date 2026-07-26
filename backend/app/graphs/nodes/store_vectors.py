"""`store_vectors` node — docs/05-ai-agent-design.md §3.2.

Persists embedded chunks into `knowledge_chunks` (docs/07-database-design.md §3.5)
through the repository layer — graphs never issue SQL/ORM queries directly
(docs/11-coding-standard.md §4). The `AsyncSession` is injected via LangGraph's
`config["configurable"]` (docs/11-coding-standard.md §4's "repositories injected or
passed via state/context"), not a module-level singleton, since each concurrent
document in the ingestion batch (`INGESTION_CONCURRENCY`) needs its own session.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.graphs.ingestion_state import IngestionState
from app.models.knowledge_chunk import KnowledgeChunk
from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository

logger = logging.getLogger(__name__)


async def store_vectors(state: IngestionState, config: RunnableConfig) -> dict[str, Any]:
    session: AsyncSession = config["configurable"]["session"]
    try:
        rows = [
            KnowledgeChunk(
                document_id=state["document_id"],
                content=chunk["content"],
                page_number=chunk["page_number"],
                chunk_index=chunk["chunk_index"],
                embedding=embedding,
            )
            for chunk, embedding in zip(state["chunks"], state["embeddings"], strict=True)
        ]
        await KnowledgeChunkRepository(session).bulk_create(rows)
        return {}
    except Exception as exc:
        logger.warning(
            "store_vectors failed for document_id=%s", state.get("document_id"), exc_info=True
        )
        return {"status": "failed", "error": f"store_vectors: {exc}"}
