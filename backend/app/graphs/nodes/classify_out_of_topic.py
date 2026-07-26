"""`classify_out_of_topic` node — docs/05-ai-agent-design.md §2.2/§2.4.

Positioned before `embed_question` per the non-negotiable short-circuit ordering
(docs/IMPLEMENTATION_PLAN.md §3: "greeting -> (Operator only) add-knowledge-intent ->
out-of-topic -> similarity threshold -> RAG"), so this is a cheap heuristic rather than an
embedding-based comparison (docs/05-ai-agent-design.md §2.4's alternative, which would
require running after `embed_question` — incompatible with the fixed node order).
"""

from __future__ import annotations

from typing import Any

from app.graphs.canned_responses import is_out_of_topic
from app.graphs.chat_state import ChatState


def classify_out_of_topic(state: ChatState) -> dict[str, Any]:
    return {"is_out_of_topic": is_out_of_topic(state["question"])}
