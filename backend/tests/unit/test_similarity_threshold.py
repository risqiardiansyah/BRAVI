"""Boundary tests for `check_similarity_threshold` — docs/12-testing-strategy.md §2
("Similarity threshold logic: boundary tests around SIMILARITY_SCORE_THRESHOLD").
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.graphs.chat_state import ChatState
from app.graphs.nodes.check_similarity_threshold import check_similarity_threshold


def _state_with_score(best_score: float | None) -> ChatState:
    return {"best_score": best_score}  # type: ignore[typeddict-item]


def test_no_matches_is_below_threshold() -> None:
    assert check_similarity_threshold(_state_with_score(None)) == "below_threshold"


def test_score_exactly_at_threshold_continues() -> None:
    assert check_similarity_threshold(_state_with_score(settings.SIMILARITY_SCORE_THRESHOLD)) == (
        "continue"
    )


@pytest.mark.parametrize("delta", [0.001, 0.1, 0.5])
def test_score_just_below_threshold_short_circuits(delta: float) -> None:
    state = _state_with_score(settings.SIMILARITY_SCORE_THRESHOLD - delta)
    assert check_similarity_threshold(state) == "below_threshold"


@pytest.mark.parametrize("delta", [0.001, 0.1, 0.25])
def test_score_above_threshold_continues(delta: float) -> None:
    state = _state_with_score(settings.SIMILARITY_SCORE_THRESHOLD + delta)
    assert check_similarity_threshold(state) == "continue"
