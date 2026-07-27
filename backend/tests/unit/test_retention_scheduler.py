"""Unit tests for `app.jobs.retention_scheduler` — mirrors
`tests/unit/test_ingestion_scheduler.py`'s pattern: the retention cleanup job must run
on a cron schedule (`RETENTION_CRON_SCHEDULE`), never immediately when the
process/container starts (docs/IMPLEMENTATION_PLAN.md Phase 13's dated note).
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.jobs import retention_scheduler


async def test_build_scheduler_does_not_invoke_the_job_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    async def fake_run_retention_cleanup() -> object:
        nonlocal call_count
        call_count += 1
        return None

    monkeypatch.setattr(retention_scheduler, "run_retention_cleanup", fake_run_retention_cleanup)

    scheduler = retention_scheduler.build_scheduler()
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
    monkeypatch.setattr(settings, "RETENTION_CRON_SCHEDULE", "15 4 * * 1-5")

    scheduler = retention_scheduler.build_scheduler()
    job = scheduler.get_job(retention_scheduler.JOB_ID)

    assert job is not None
    trigger_str = str(job.trigger)
    assert "minute='15'" in trigger_str
    assert "hour='4'" in trigger_str
    assert "day_of_week='1-5'" in trigger_str


def test_job_configured_to_never_overlap_and_to_coalesce_missed_runs() -> None:
    scheduler = retention_scheduler.build_scheduler()
    job = scheduler.get_job(retention_scheduler.JOB_ID)

    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True
