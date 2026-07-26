"""`check_similarity_threshold` — docs/05-ai-agent-design.md §2.2/§2.3.

A pure routing function, not a state-mutating graph node — `best_score` is already computed
by `similarity_search`, so there is nothing new to write to state; this only decides which
edge the graph takes next (docs/11-coding-standard.md §8: "routing/composition happens only
via graph edges"), mirroring `graphs/ingestion_graph.py`'s own `_route_after` pattern.
"""

from __future__ import annotations

from typing import Literal

from app.config import settings
from app.graphs.chat_state import ChatState


def check_similarity_threshold(state: ChatState) -> Literal["below_threshold", "continue"]:
    best_score = state.get("best_score")
    if best_score is None or best_score < settings.SIMILARITY_SCORE_THRESHOLD:
        return "below_threshold"
    return "continue"
