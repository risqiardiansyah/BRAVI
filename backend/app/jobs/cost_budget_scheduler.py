"""Cron-scheduled wrapper around the daily cost-budget check.

Mirrors `app/jobs/retention_scheduler.py`'s pattern (docs/IMPLEMENTATION_PLAN.md
Phase 13's dated notes): this module does **not** invoke `run_cost_budget_check()` when
it starts; it only runs it at each occurrence of `COST_BUDGET_CRON_SCHEDULE` (a 5-field
cron expression, evaluated in UTC, default `"0 * * * *"` — hourly).

`max_instances=1`/`coalesce=True` for the same reason as the other cron jobs: no
overlapping runs, and only one catch-up run after downtime spanning multiple missed
occurrences.
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.services.cost_budget_service import run_cost_budget_check

logger = logging.getLogger(__name__)

JOB_ID = "cost_budget_check"


def build_scheduler() -> AsyncIOScheduler:
    """Builds and configures (but does not start) the scheduler — split out from
    `main()` so tests can inspect the registered job/trigger without needing a running
    event loop or waiting on a real cron fire time."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    trigger = CronTrigger.from_crontab(settings.COST_BUDGET_CRON_SCHEDULE, timezone="UTC")
    scheduler.add_job(
        run_cost_budget_check,
        trigger=trigger,
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
    )
    return scheduler


async def main() -> None:
    logging.basicConfig(level=settings.LOG_LEVEL)
    scheduler = build_scheduler()
    scheduler.start()
    job = scheduler.get_job(JOB_ID)
    logger.info(
        "cost budget scheduler started — not running immediately, waiting for next cron fire",
        extra={
            "cron_schedule": settings.COST_BUDGET_CRON_SCHEDULE,
            "next_run_time": str(job.next_run_time) if job else None,
        },
    )
    try:
        await asyncio.Event().wait()  # run forever; container exits on SIGTERM
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
