"""Repository for `usage_metrics` — docs/07-database-design.md §3.7."""

from __future__ import annotations

from app.models.usage_metric import UsageMetric
from app.repositories.base import BaseRepository


class UsageMetricRepository(BaseRepository[UsageMetric]):
    model = UsageMetric
