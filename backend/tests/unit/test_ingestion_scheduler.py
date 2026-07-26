"""Unit tests for `app.jobs.ingestion_scheduler` — user-directed deviation from
docs/10-deployment.md §4's original "run once at deploy time" model
(docs/IMPLEMENTATION_PLAN.md Phase 6's dated correction note): the ingestion job must
run on a cron schedule (`INGESTION_CRON_SCHEDULE`), never immediately when the
process/container starts.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.jobs import ingestion_scheduler


async def test_build_scheduler_does_not_invoke_the_job_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    async def fake_run_initial_ingestion() -> list[object]:
        nonlocal call_count
        call_count += 1
        return []

    monkeypatch.setattr(ingestion_scheduler, "run_initial_ingestion", fake_run_initial_ingestion)

    scheduler = ingestion_scheduler.build_scheduler()
    assert call_count == 0  # building the scheduler alone must never run the job

    scheduler.start()
    try:
        await asyncio.sleep(0.1)
        assert call_count == 0  # starting it (without a due fire time) must not either
    finally:
        scheduler.shutdown(wait=False)


def test_job_registered_with_the_configured_cron_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "INGESTION_CRON_SCHEDULE", "15 3 * * 1-5")

    scheduler = ingestion_scheduler.build_scheduler()
    job = scheduler.get_job(ingestion_scheduler.JOB_ID)

    assert job is not None
    trigger_str = str(job.trigger)
    assert "minute='15'" in trigger_str
    assert "hour='3'" in trigger_str
    assert "day_of_week='1-5'" in trigger_str


def test_job_configured_to_never_overlap_and_to_coalesce_missed_runs() -> None:
    """`max_instances=1`/`coalesce=True` — docs/07-database-design.md §5's idempotency
    makes overlapping runs safe either way, but explicit is better than relying on the
    scheduler library's default, especially since a full run can take a long time at
    large source-list sizes (the very reason `app` no longer waits on this job at all,
    docs/IMPLEMENTATION_PLAN.md Phase 6's correction note)."""
    scheduler = ingestion_scheduler.build_scheduler()
    job = scheduler.get_job(ingestion_scheduler.JOB_ID)

    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True
