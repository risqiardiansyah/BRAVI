"""Unit tests for `app.jobs.cost_budget_scheduler` — mirrors
`tests/unit/test_retention_scheduler.py`'s pattern: the cost-budget check must run on a
cron schedule (`COST_BUDGET_CRON_SCHEDULE`), never immediately when the
process/container starts (docs/IMPLEMENTATION_PLAN.md Phase 13 task 4).
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.jobs import cost_budget_scheduler


async def test_build_scheduler_does_not_invoke_the_job_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    async def fake_run_cost_budget_check() -> object:
        nonlocal call_count
        call_count += 1
        return None

    monkeypatch.setattr(cost_budget_scheduler, "run_cost_budget_check", fake_run_cost_budget_check)

    scheduler = cost_budget_scheduler.build_scheduler()
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
    monkeypatch.setattr(settings, "COST_BUDGET_CRON_SCHEDULE", "30 * * * *")

    scheduler = cost_budget_scheduler.build_scheduler()
    job = scheduler.get_job(cost_budget_scheduler.JOB_ID)

    assert job is not None
    assert "minute='30'" in str(job.trigger)


def test_job_configured_to_never_overlap_and_to_coalesce_missed_runs() -> None:
    scheduler = cost_budget_scheduler.build_scheduler()
    job = scheduler.get_job(cost_budget_scheduler.JOB_ID)

    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True
