"""`app.utils.pricing` — docs/19-cost-management.md §2 (cost-calculation mechanism
gap-fill), docs/IMPLEMENTATION_PLAN.md Phase 13 task 4.
"""

from __future__ import annotations

from pathlib import Path

from app.utils.pricing import ModelRate, _load_pricing_table, estimate_cost_usd


def test_estimate_cost_usd_returns_none_when_model_id_is_none() -> None:
    assert estimate_cost_usd(model_id=None, input_tokens=1000, output_tokens=1000) is None


def test_estimate_cost_usd_returns_none_for_unknown_model() -> None:
    result = estimate_cost_usd(
        model_id="not-a-configured-model", input_tokens=100, output_tokens=100
    )
    assert result is None


def test_estimate_cost_usd_computes_from_the_real_pricing_table() -> None:
    # Uses the actual bundled `app/bedrock_pricing.yaml` — proves the file loads and
    # the formula matches docs/19-cost-management.md §2 exactly for a real entry.
    result = estimate_cost_usd(
        model_id="global.anthropic.claude-sonnet-4-6", input_tokens=1000, output_tokens=1000
    )
    assert result == 3.0 + 15.0


def test_estimate_cost_usd_treats_missing_token_counts_as_zero() -> None:
    result = estimate_cost_usd(
        model_id="global.anthropic.claude-sonnet-4-6", input_tokens=None, output_tokens=None
    )
    assert result == 0.0


def test_load_pricing_table_returns_model_rates() -> None:
    table = _load_pricing_table(
        Path(__file__).resolve().parents[2] / "app" / "bedrock_pricing.yaml"
    )
    assert "global.anthropic.claude-sonnet-4-6" in table
    rate = table["global.anthropic.claude-sonnet-4-6"]
    assert isinstance(rate, ModelRate)
    assert rate.input_per_1k_tokens_usd == 3.0


def test_load_pricing_table_missing_file_returns_empty(tmp_path: Path) -> None:
    table = _load_pricing_table(tmp_path / "does-not-exist.yaml")
    assert table == {}
