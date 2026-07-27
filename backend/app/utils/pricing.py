"""Bedrock $ pricing table — docs/19-cost-management.md §2 (gap fill).

Loaded once at import time from `app/bedrock_pricing.yaml`, a config file (not Python
code) so a pricing change is a config update + redeploy, never a code change — AWS
pricing changes independently of this application's release cycle. `graphs/nodes/
log_chat_metrics.py` calls `estimate_cost_usd` to populate `usage_metrics.
estimated_cost_usd` (previously left `NULL` — see docs/IMPLEMENTATION_PLAN.md Phase 9/11
completion notes).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_PRICING_FILE = Path(__file__).resolve().parent.parent / "bedrock_pricing.yaml"


@dataclass(frozen=True)
class ModelRate:
    input_per_1k_tokens_usd: float
    output_per_1k_tokens_usd: float


def _load_pricing_table(path: Path) -> dict[str, ModelRate]:
    if not path.exists():
        logger.warning("Bedrock pricing file not found at %s — cost estimates will be $0.", path)
        return {}
    with path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    table: dict[str, ModelRate] = {}
    for model_id, rates in (raw.get("models") or {}).items():
        table[model_id] = ModelRate(
            input_per_1k_tokens_usd=float(rates["input_per_1k_tokens_usd"]),
            output_per_1k_tokens_usd=float(rates["output_per_1k_tokens_usd"]),
        )
    return table


_PRICING_TABLE = _load_pricing_table(_PRICING_FILE)


def estimate_cost_usd(
    *, model_id: str | None, input_tokens: int | None, output_tokens: int | None
) -> float | None:
    """`(input_tokens / 1000 * input_rate) + (output_tokens / 1000 * output_rate)`
    (docs/19-cost-management.md §2), using the rate row for `model_id`. Returns `None`
    when `model_id` is `None` (no Bedrock call happened this turn — a short-circuited
    request is attributed zero cost, docs/07-database-design.md §3.7) or when no rate
    row is configured for it (logged once per occurrence rather than silently defaulting
    to zero, so a missing pricing entry is visible in production logs)."""
    if model_id is None:
        return None
    rate = _PRICING_TABLE.get(model_id)
    if rate is None:
        logger.warning("No Bedrock pricing configured for model_id=%s", model_id)
        return None
    return (input_tokens or 0) / 1000 * rate.input_per_1k_tokens_usd + (
        output_tokens or 0
    ) / 1000 * rate.output_per_1k_tokens_usd
