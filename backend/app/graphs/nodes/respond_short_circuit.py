"""Short-circuit "respond_*" nodes — docs/05-ai-agent-design.md §2.2/§2.5.

Each sets the canned Bahasa Indonesia answer plus the short-circuit bookkeeping fields; no
Bedrock call, no `top_matches`/`sources` (routed straight to `persist_message`, skipping
`append_sources` — there is nothing to cite). Shared between `user_chat_graph` and
`operator_chat_graph`; the Operator-only `respond_add_knowledge_template` counterpart is
built in Phase 10 alongside `classify_add_knowledge_intent`.
"""

from __future__ import annotations

from typing import Any

from app.graphs.canned_responses import (
    GREETING_RESPONSE,
    NO_KNOWLEDGE_FOUND_RESPONSE,
    OUT_OF_TOPIC_RESPONSE,
)
from app.graphs.chat_state import ChatState


def respond_default_greeting(state: ChatState) -> dict[str, Any]:
    return {
        "answer": GREETING_RESPONSE,
        "sources": [],
        "short_circuited": True,
        "short_circuit_reason": "greeting",
        "mode": None,
    }


def respond_out_of_topic(state: ChatState) -> dict[str, Any]:
    return {
        "answer": OUT_OF_TOPIC_RESPONSE,
        "sources": [],
        "short_circuited": True,
        "short_circuit_reason": "out_of_topic",
        "mode": None,
    }


def respond_no_knowledge_found(state: ChatState) -> dict[str, Any]:
    return {
        "answer": NO_KNOWLEDGE_FOUND_RESPONSE,
        "sources": [],
        "short_circuited": True,
        "short_circuit_reason": "low_similarity",
        "mode": None,
    }
