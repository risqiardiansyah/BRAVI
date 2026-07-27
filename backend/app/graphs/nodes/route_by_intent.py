"""`route_by_intent` — docs/05-ai-agent-design.md §2.2/§2.3, `operator_chat_graph` only.

A pure routing function, not a state-mutating node — mirrors `check_similarity_threshold`'s
precedent (Phase 9): deciding which edge to take needs nothing beyond `state["question"]`,
so there is nothing new to write to state here (docs/11-coding-standard.md §8: "routing/
composition happens only via graph edges"). `generate_answer`/`generate_summary` each set
`mode` on their own output once they know which of the two actually ran, rather than this
function writing a `mode` field ahead of time.

Rule-based bilingual keyword match for a summarization request (e.g. "ringkas", "rangkum",
"summary") — near-zero latency, zero LLM cost, the same class of heuristic as
`classify_out_of_topic`.
"""

from __future__ import annotations

import re
from typing import Literal

from app.graphs.chat_state import ChatState

_SUMMARY_INTENT_PATTERNS = (
    r"\bringkas(an|kan)?\b",
    r"\brangkum(an|kan)?\b",
    r"\bsummar(y|ies|ize|ise|ization)\b",
)
_SUMMARY_INTENT_RE = re.compile("|".join(_SUMMARY_INTENT_PATTERNS), re.IGNORECASE)


def route_by_intent(state: ChatState) -> Literal["summary", "qa"]:
    return "summary" if _SUMMARY_INTENT_RE.search(state["question"]) else "qa"
