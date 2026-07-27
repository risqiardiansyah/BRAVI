"""`classify_add_knowledge_intent` node — docs/05-ai-agent-design.md §2.2/§2.3,
`operator_chat_graph` only.

Rule-based bilingual keyword/phrase match, zero Bedrock cost, near-zero latency — same
pattern as `classify_greeting`. Positioned right after `classify_greeting` in
`operator_chat_graph`'s node order (docs/11-coding-standard.md §8.1's canonical backbone
list). This node function lives under the shared `graphs/nodes/` package by convention,
but only `operator_chat_graph.py` ever wires it — `user_chat_graph.py` must never import
it (docs/11-coding-standard.md §8.1).
"""

from __future__ import annotations

from typing import Any

from app.graphs.canned_responses import is_add_knowledge_intent
from app.graphs.chat_state import ChatState


def classify_add_knowledge_intent(state: ChatState) -> dict[str, Any]:
    return {"is_add_knowledge_intent": is_add_knowledge_intent(state["question"])}
