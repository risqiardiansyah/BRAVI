"""Daily cost-budget check — docs/19-cost-management.md §4, docs/09-observability.md
§7 ("Daily estimated cost exceeds budget threshold (`DAILY_COST_BUDGET_USD`) -> Notify").

Sums `usage_metrics.estimated_cost_usd` for the current UTC calendar day and compares it
against `DAILY_COST_BUDGET_USD` (`docs/10-deployment.md` §3 — unset means no budget
alert, `app/config.py`'s `_blank_budget_is_unset` validator). No notification channel
(email/Slack/PagerDuty/etc.) is specified anywhere in the docs for this alert — "Notify"
is implemented as a `WARNING`-level structured log line (`docs/09-observability.md` §3's
own log-level table: "WARNING (retries, degraded)") plus the `daily_cost_budget_exceeded`
Prometheus gauge (`app/utils/metrics.py`), which a real alerting stack (e.g. Alertmanager)
can page on — consistent with how every other "Notify" row in `09-observability.md` §7 is
left to the monitoring stack rather than this application sending notifications itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import settings
from app.db import AsyncSessionLocal
from app.repositories.usage_metric_repository import UsageMetricRepository
from app.utils.metrics import daily_cost_budget_exceeded

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CostBudgetCheckResult:
    total_cost_usd: float
    budget_usd: float | None
    exceeded: bool


async def run_cost_budget_check() -> CostBudgetCheckResult:
    """Runs one check against the current UTC calendar day (00:00 UTC through now).
    Always a no-op result (`exceeded=False`) when `DAILY_COST_BUDGET_USD` is unset —
    the gauge is still reset to `0` so a previously-tripped alert clears if the budget
    setting is removed without a restart."""
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with AsyncSessionLocal() as session:
        total_cost_usd = await UsageMetricRepository(session).total_estimated_cost(
            since=day_start, until=now
        )

    budget_usd = settings.DAILY_COST_BUDGET_USD
    exceeded = budget_usd is not None and total_cost_usd >= budget_usd
    daily_cost_budget_exceeded.set(1 if exceeded else 0)

    log_fields = {
        "total_cost_usd": total_cost_usd,
        "budget_usd": budget_usd,
        "date": day_start.date().isoformat(),
    }
    if exceeded:
        logger.warning("daily cost budget exceeded", extra=log_fields)
    else:
        logger.info("daily cost budget check passed", extra=log_fields)

    return CostBudgetCheckResult(
        total_cost_usd=total_cost_usd, budget_usd=budget_usd, exceeded=exceeded
    )
