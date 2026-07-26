"""`update_ingestion_status` node — docs/05-ai-agent-design.md §3.2.

Shared terminal node for both the success and failure paths (routed here directly on
failure by `graphs/ingestion_graph.py`'s conditional edges, skipping whichever nodes
didn't run) — persists the final status onto `knowledge_documents`/`ingestion_jobs`,
and, for startup-batch documents only, `knowledge_sources.is_ingested`/`content_hash`
— but only on success. A failed attempt leaves the source row unchanged so the next
run retries it (docs/07-database-design.md §5).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.graphs.ingestion_state import IngestionState
from app.repositories.ingestion_job_repository import IngestionJobRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from app.repositories.knowledge_source_repository import KnowledgeSourceRepository

logger = logging.getLogger(__name__)


async def update_ingestion_status(state: IngestionState, config: RunnableConfig) -> dict[str, Any]:
    session: AsyncSession = config["configurable"]["session"]
    document_repo = KnowledgeDocumentRepository(session)
    job_repo = IngestionJobRepository(session)

    document = await document_repo.get_by_id(state["document_id"])
    job = await job_repo.get_by_id(state["job_id"])
    if document is None or job is None:
        # Both rows are created by the caller before the graph is ever invoked
        # (docs/05-ai-agent-design.md §3.1's document_id/job_id) — their absence here
        # means the caller violated that contract, not a recoverable ingestion failure.
        raise RuntimeError(
            f"update_ingestion_status: document_id={state.get('document_id')} or "
            f"job_id={state.get('job_id')} not found — caller must create both rows "
            "before invoking ingestion_graph"
        )

    if state.get("status") == "failed":
        error_message = state.get("error") or "unknown error"
        await document_repo.mark_failed(document, error_message=error_message)
        await job_repo.mark_failed(job, error_message=error_message)
        return {"status": "failed"}

    chunk_count = len(state["chunks"])
    await document_repo.mark_completed(document, chunk_count=chunk_count)
    await job_repo.mark_completed(job)

    source_id = state.get("knowledge_source_id")
    content_hash = state.get("content_hash")
    if source_id is not None and content_hash is not None:
        source_repo = KnowledgeSourceRepository(session)
        source = await source_repo.get_by_id(source_id)
        if source is not None:
            await source_repo.mark_ingested(source, content_hash=content_hash)

    return {"status": "completed"}
