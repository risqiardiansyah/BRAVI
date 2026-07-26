"""Startup ingestion job — docs/04-system-architecture.md §5, docs/07-database-design.md §5.

Runs as a separate one-off step, not inline-blocking in the web process
(docs/10-deployment.md §4's recommendation) — invoked via
`python -m app.jobs.run_initial_ingestion` before the API starts accepting traffic
(wired as its own `docker-compose.yml` service that the `app` service's `depends_on`
waits on).

**Idempotency** (docs/07-database-design.md §5): every run re-downloads and re-hashes
every `knowledge_sources` row's current content, regardless of `is_ingested` — an
unchanged, already-ingested source is a cheap no-op (no new `knowledge_documents`
row); a source whose content changed since the last run is always re-ingested.
`INGESTION_RUN_ONCE` deliberately does **not** gate a persisted "has the batch ever
run" marker — that would mean a source's content change is never picked up again
without a manual DB edit, defeating the point of hash-based change detection
(user-confirmed decision, docs/IMPLEMENTATION_PLAN.md Phase 6 note).

Up to `INGESTION_CONCURRENCY` sources are processed concurrently
(docs/05-ai-agent-design.md §3.2), each on its own `AsyncSession` (sessions are not
safe to share across concurrent tasks). A source failing to download/parse/embed
fails only that source — the batch completes for every other source
(docs/05-ai-agent-design.md §3.2, §07-database-design.md §5 FR-10 item 6).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import AsyncSessionLocal
from app.graphs.ingestion_graph import ingestion_graph
from app.graphs.ingestion_state import IngestionState
from app.models.ingestion_job import IngestionJob
from app.models.knowledge_document import KnowledgeDocument
from app.repositories.ingestion_job_repository import IngestionJobRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from app.repositories.knowledge_source_repository import KnowledgeSourceRepository
from app.utils.http_download import download_bytes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceOutcome:
    relative_path: str
    outcome: str  # "completed" | "failed" | "skipped_unchanged" | "check_failed"
    detail: str | None = None


def _build_source_url(relative_path: str) -> str:
    """Joins `DOCUMENT_BASE_URL` + `relative_path`, rejecting path-traversal attempts
    in the (DB-sourced, not end-user-request-sourced, but still worth guarding)
    `relative_path` value."""
    if not settings.DOCUMENT_BASE_URL:
        raise RuntimeError("DOCUMENT_BASE_URL is not configured")
    if ".." in relative_path.replace("\\", "/").split("/"):
        raise ValueError(f"unsafe relative_path (path traversal): {relative_path!r}")
    return f"{settings.DOCUMENT_BASE_URL.rstrip('/')}/{relative_path.lstrip('/')}"


async def _record_failed_document(
    session: AsyncSession, *, source_id: uuid.UUID, title: str, source_url: str | None, error: str
) -> None:
    document = await KnowledgeDocumentRepository(session).create(
        KnowledgeDocument(
            source_id=source_id,
            title=title,
            source_url=source_url,
            source_type="url",
            status="failed",
            error_message=error,
        )
    )
    await IngestionJobRepository(session).create(
        IngestionJob(
            job_type="startup_batch",
            document_id=document.id,
            status="failed",
            error_message=error,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
    )
    await session.commit()


async def _process_source(source_id: uuid.UUID, semaphore: asyncio.Semaphore) -> SourceOutcome:
    async with semaphore, AsyncSessionLocal() as session:
        source_repo = KnowledgeSourceRepository(session)
        source = await source_repo.get_by_id(source_id)
        if source is None:
            return SourceOutcome(str(source_id), "failed", "source row no longer exists")

        url: str | None = None
        try:
            url = _build_source_url(source.relative_path)
            raw_bytes = await download_bytes(url)
        except Exception as exc:
            if source.is_ingested:
                # Already ingested successfully before; a transient failure while
                # re-checking for content changes is not a new ingestion failure —
                # nothing to persist, this source is simply retried on the next run.
                logger.warning(
                    "startup ingestion: could not re-check '%s' for changes: %s",
                    source.relative_path,
                    exc,
                )
                return SourceOutcome(source.relative_path, "check_failed", str(exc))
            await _record_failed_document(
                session,
                source_id=source.id,
                title=source.title or source.relative_path,
                source_url=url,
                error=f"download failed: {exc}",
            )
            return SourceOutcome(source.relative_path, "failed", str(exc))

        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        if source.is_ingested and source.content_hash == content_hash:
            return SourceOutcome(source.relative_path, "skipped_unchanged")

        document = await KnowledgeDocumentRepository(session).create(
            KnowledgeDocument(
                source_id=source.id,
                title=source.title or source.relative_path,
                source_url=url,
                source_type="url",
                status="queued",
            )
        )
        job = await IngestionJobRepository(session).create(
            IngestionJob(
                job_type="startup_batch",
                document_id=document.id,
                status="processing",
                started_at=datetime.now(UTC),
            )
        )
        await session.flush()

        initial_state: IngestionState = {
            "source_type": "url",
            "source_ref": url,
            "document_id": document.id,
            "job_id": job.id,
            "knowledge_source_id": source.id,
            "content_hash": content_hash,
            "raw_bytes": raw_bytes,
        }
        try:
            final_state = await ingestion_graph.ainvoke(
                initial_state, config={"configurable": {"session": session}}
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.exception(
                "startup ingestion: unexpected error processing '%s'", source.relative_path
            )
            return SourceOutcome(source.relative_path, "failed", str(exc))

        final_status = final_state.get("status")
        return SourceOutcome(
            source.relative_path,
            "completed" if final_status == "completed" else "failed",
            final_state.get("error"),
        )


async def run_initial_ingestion(source_ids: list[uuid.UUID] | None = None) -> list[SourceOutcome]:
    """Runs the idempotent ingestion pass over `knowledge_sources`.

    `source_ids` restricts the run to specific rows — used by
    `tests/integration/test_startup_ingestion_idempotency.py` so it only ever touches
    the row it seeded itself, never any other `knowledge_sources` row already present
    in the database (e.g. real, operator-managed sources). The default (`None`, every
    real invocation via `python -m app.jobs.run_initial_ingestion`) processes every row.
    """
    if source_ids is None:
        async with AsyncSessionLocal() as session:
            sources = await KnowledgeSourceRepository(session).list_all()
            source_ids = [source.id for source in sources]

    semaphore = asyncio.Semaphore(settings.INGESTION_CONCURRENCY)
    outcomes = list(await asyncio.gather(*(_process_source(sid, semaphore) for sid in source_ids)))

    tally: dict[str, int] = {}
    for outcome in outcomes:
        tally[outcome.outcome] = tally.get(outcome.outcome, 0) + 1
    logger.info(
        "startup ingestion job finished",
        extra={"total_sources": len(outcomes), **tally},
    )
    return outcomes


async def main() -> None:
    logging.basicConfig(level=settings.LOG_LEVEL)
    await run_initial_ingestion()


if __name__ == "__main__":
    asyncio.run(main())
