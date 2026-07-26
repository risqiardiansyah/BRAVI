"""Cron-scheduled wrapper around the startup ingestion job.

User-directed deviation from `10-deployment.md` §4's original "run once at deploy
time, before the API starts accepting traffic" model — see
`docs/IMPLEMENTATION_PLAN.md` Phase 6's dated correction note. This module does
**not** invoke `run_initial_ingestion()` when it starts; it only runs it at each
occurrence of `INGESTION_CRON_SCHEDULE` (a 5-field cron expression, evaluated in
UTC, default `"0 2 * * *"` — daily at 02:00 UTC). An ad hoc/manual run is still
available via `python -m app.jobs.run_initial_ingestion` directly.

`max_instances=1` (docs/07-database-design.md §5's idempotency already makes this
safe either way, but explicit is better than relying on the scheduler library's
default): if a run is still in progress when the next scheduled fire time arrives
(realistic at large source-list sizes), APScheduler skips that occurrence rather
than starting an overlapping second run. `coalesce=True`: if the process was down
across more than one missed occurrence, only one catch-up run fires when it comes
back, not one per missed occurrence.
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.jobs.run_initial_ingestion import run_initial_ingestion

logger = logging.getLogger(__name__)

JOB_ID = "startup_ingestion"


def build_scheduler() -> AsyncIOScheduler:
    """Builds and configures (but does not start) the scheduler — split out from
    `main()` so tests can inspect the registered job/trigger without needing a
    running event loop or waiting on a real cron fire time."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    trigger = CronTrigger.from_crontab(settings.INGESTION_CRON_SCHEDULE, timezone="UTC")
    scheduler.add_job(
        run_initial_ingestion,
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
        "ingestion scheduler started — not running immediately, waiting for next cron fire",
        extra={
            "cron_schedule": settings.INGESTION_CRON_SCHEDULE,
            "next_run_time": str(job.next_run_time) if job else None,
        },
    )
    try:
        await asyncio.Event().wait()  # run forever; container exits on SIGTERM
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
