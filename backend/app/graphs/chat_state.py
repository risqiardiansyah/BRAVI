"""`user_chat_graph`/`operator_chat_graph` shared state schema — docs/05-ai-agent-design.md §2.1.

The doc's own schema is explicitly marked "conceptual"; this extends it the same way
`graphs/ingestion_state.py` (Phase 6) extended `IngestionState` — keeping every documented
field name/meaning, adding what the real pipeline needs to pass between nodes (the merged
"working" question vs. the original as-typed text, per-node model-usage flags for
`log_metrics`, and the internal timer for the aggregate `usage_metrics.latency_ms`).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal, TypedDict


class TopMatch(TypedDict):
    """One `similarity_search` result — docs/18-rag-design.md §4's retrieval query,
    joined with `07-database-design.md` §3.4's freshness/versioning columns."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    score: float
    page_number: int | None
    title: str | None
    source_url: str | None
    valid_until: date | None
    superseded_by_title: str | None


class SourceItem(TypedDict):
    """One entry of the `done` SSE event's `sources` array — docs/06-api-specification.md §0."""

    document_id: uuid.UUID
    title: str
    url: str | None
    page: int | None
    valid_until: date | None
    superseded_by_title: str | None


class ChatState(TypedDict, total=False):
    session_id: uuid.UUID
    user_id: str
    persona: Literal["user", "operator"]

    # `question` is the mutable working copy nodes read/enrich (e.g. `preprocess_input`
    # merges an image description into it); `original_question` is what the user actually
    # typed, persisted verbatim as the `messages`/`usage_metrics` row content — never
    # overwritten — so image-description text never pollutes chat history or trending
    # analytics (docs/06-api-specification.md §4, Phase 11).
    question: str
    original_question: str

    image_bytes: bytes | None
    image_format: Literal["png", "jpeg", "webp"] | None
    image_description: str | None

    is_greeting: bool
    is_add_knowledge_intent: bool  # operator_chat_graph only; always unset in user_chat_graph
    is_out_of_topic: bool

    question_embedding: list[float]
    top_matches: list[TopMatch]
    best_score: float | None

    history_summary: str | None

    mode: Literal["qa", "summary"] | None
    answer: str | None
    sources: list[SourceItem]

    short_circuited: bool
    short_circuit_reason: str | None

    # Observability bookkeeping for `log_metrics` (docs/05-ai-agent-design.md §2.3) —
    # aggregate latency only; a full per-node latency_ms breakdown is not persisted
    # anywhere in `07-database-design.md` §3.7's `usage_metrics` schema (one `latency_ms`
    # INT column), so it is not tracked as a dict here either.
    started_monotonic: float
    embedding_model_used: str | None
    text_model_used: str | None
    input_tokens: int | None
    output_tokens: int | None

    # Time to first streamed token, in ms, measured from `started_monotonic` to the first
    # `generate_answer`/`generate_summary` chunk (docs/03-non-functional-requirements.md §1
    # TTFT target, docs/20-performance-target.md §3). Only ever set on the full-RAG path —
    # short-circuit tiers emit their single canned/answer token in one shot at the SSE
    # layer (`services/chat_service.py::_stream_chat_graph`), where "time to first token"
    # and "total latency" are the same number, so a separate TTFT is not meaningful there.
    ttft_ms: int | None
