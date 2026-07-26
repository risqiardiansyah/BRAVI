"""`classify_greeting` node — docs/05-ai-agent-design.md §2.2/§2.3.

Rule-based, zero Bedrock cost, near-zero latency. Shared between `user_chat_graph` and
`operator_chat_graph` (docs/11-coding-standard.md §8.1).
"""

from __future__ import annotations

from typing import Any

from app.graphs.canned_responses import is_greeting
from app.graphs.chat_state import ChatState


def classify_greeting(state: ChatState) -> dict[str, Any]:
    return {"is_greeting": is_greeting(state["question"])}
