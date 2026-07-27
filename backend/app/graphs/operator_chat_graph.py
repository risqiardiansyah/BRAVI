"""`operator_chat_graph` — docs/05-ai-agent-design.md §2.2, docs/11-coding-standard.md
§8.1.

Reuses every shared QA node built by `user_chat_graph.py` (Phase 9) and additionally wires
`classify_add_knowledge_intent` (right after `classify_greeting`, before
`classify_out_of_topic` — docs/11-coding-standard.md §8.1's canonical node-order list) and
`route_by_intent` between `condense_history` and `generate_answer`/`generate_summary`. May
import both `tools/operator_tools.py` and `tools/user_tools.py`/`graphs/nodes/`
(docs/11-coding-standard.md §8.1: "Operator is a superset for QA purposes").

Node order: preprocess_input -> classify_greeting -> classify_add_knowledge_intent ->
classify_out_of_topic -> embed_question -> similarity_search ->
check_similarity_threshold -> condense_history -> route_by_intent ->
(generate_summary | generate_answer) -> append_sources -> persist_message -> log_metrics
-> END. Every short-circuit tier (greeting/add-knowledge-intent/out-of-topic/
below-threshold) routes its `respond_*` node to `persist_message`/`log_metrics` rather
than the diagram's literal (abbreviated) "-> END", mirroring `user_chat_graph.py`'s own
documented reasoning (Phase 9) — `append_sources` is skipped on every short-circuit path
since there is nothing to cite.
"""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graphs.chat_state import ChatState
from app.graphs.nodes.append_sources import append_sources
from app.graphs.nodes.check_similarity_threshold import check_similarity_threshold
from app.graphs.nodes.classify_add_knowledge_intent import classify_add_knowledge_intent
from app.graphs.nodes.classify_greeting import classify_greeting
from app.graphs.nodes.classify_out_of_topic import classify_out_of_topic
from app.graphs.nodes.condense_history import condense_history
from app.graphs.nodes.embed_question import embed_question
from app.graphs.nodes.generate_answer import generate_answer
from app.graphs.nodes.generate_summary import generate_summary
from app.graphs.nodes.log_chat_metrics import log_chat_metrics
from app.graphs.nodes.persist_message import persist_message
from app.graphs.nodes.preprocess_input import preprocess_input
from app.graphs.nodes.respond_short_circuit import (
    respond_add_knowledge_template,
    respond_default_greeting,
    respond_no_knowledge_found,
    respond_out_of_topic,
)
from app.graphs.nodes.route_by_intent import route_by_intent
from app.graphs.nodes.similarity_search import similarity_search

_AfterGreeting = Literal["respond_default_greeting", "classify_add_knowledge_intent"]
_AfterAddKnowledgeIntent = Literal["respond_add_knowledge_template", "classify_out_of_topic"]
_AfterOutOfTopic = Literal["respond_out_of_topic", "embed_question"]


def _route_after_greeting(state: ChatState) -> _AfterGreeting:
    is_greeting = state.get("is_greeting")
    return "respond_default_greeting" if is_greeting else "classify_add_knowledge_intent"


def _route_after_add_knowledge_intent(state: ChatState) -> _AfterAddKnowledgeIntent:
    is_intent = state.get("is_add_knowledge_intent")
    return "respond_add_knowledge_template" if is_intent else "classify_out_of_topic"


def _route_after_out_of_topic(state: ChatState) -> _AfterOutOfTopic:
    return "respond_out_of_topic" if state.get("is_out_of_topic") else "embed_question"


def build_operator_chat_graph() -> CompiledStateGraph[ChatState, None, ChatState, ChatState]:
    builder: StateGraph[ChatState, None, ChatState, ChatState] = StateGraph(ChatState)

    builder.add_node("preprocess_input", preprocess_input)
    builder.add_node("classify_greeting", classify_greeting)
    builder.add_node("respond_default_greeting", respond_default_greeting)
    builder.add_node("classify_add_knowledge_intent", classify_add_knowledge_intent)
    builder.add_node("respond_add_knowledge_template", respond_add_knowledge_template)
    builder.add_node("classify_out_of_topic", classify_out_of_topic)
    builder.add_node("respond_out_of_topic", respond_out_of_topic)
    builder.add_node("embed_question", embed_question)
    builder.add_node("similarity_search", similarity_search)
    builder.add_node("respond_no_knowledge_found", respond_no_knowledge_found)
    builder.add_node("condense_history", condense_history)
    builder.add_node("generate_answer", generate_answer)
    builder.add_node("generate_summary", generate_summary)
    builder.add_node("append_sources", append_sources)
    builder.add_node("persist_message", persist_message)
    builder.add_node("log_metrics", log_chat_metrics)

    builder.add_edge(START, "preprocess_input")
    builder.add_edge("preprocess_input", "classify_greeting")
    builder.add_conditional_edges(
        "classify_greeting",
        _route_after_greeting,
        ["respond_default_greeting", "classify_add_knowledge_intent"],
    )
    builder.add_conditional_edges(
        "classify_add_knowledge_intent",
        _route_after_add_knowledge_intent,
        ["respond_add_knowledge_template", "classify_out_of_topic"],
    )
    builder.add_conditional_edges(
        "classify_out_of_topic",
        _route_after_out_of_topic,
        ["respond_out_of_topic", "embed_question"],
    )
    builder.add_edge("embed_question", "similarity_search")
    builder.add_conditional_edges(
        "similarity_search",
        check_similarity_threshold,
        {"below_threshold": "respond_no_knowledge_found", "continue": "condense_history"},
    )
    builder.add_conditional_edges(
        "condense_history",
        route_by_intent,
        {"summary": "generate_summary", "qa": "generate_answer"},
    )
    builder.add_edge("generate_answer", "append_sources")
    builder.add_edge("generate_summary", "append_sources")
    builder.add_edge("append_sources", "persist_message")

    builder.add_edge("respond_default_greeting", "persist_message")
    builder.add_edge("respond_add_knowledge_template", "persist_message")
    builder.add_edge("respond_out_of_topic", "persist_message")
    builder.add_edge("respond_no_knowledge_found", "persist_message")

    builder.add_edge("persist_message", "log_metrics")
    builder.add_edge("log_metrics", END)

    return builder.compile()


operator_chat_graph = build_operator_chat_graph()
