"""`log_metrics` node for the chat graphs — docs/05-ai-agent-design.md §2.2/§2.3.

Named `log_chat_metrics` (not `log_metrics`) at the file/function level only to avoid
colliding with `graphs/nodes/log_metrics.py` (Phase 6's ingestion-graph node in the same
`graphs/nodes/` package); the node is still registered under the graph key `"log_metrics"`
in `user_chat_graph.py`, matching the doc's node name. Writes one `usage_metrics` row
(docs/07-database-design.md §3.7) plus a structured log line — the last node on every path,
short-circuited or not, so short-circuit-tier analytics (`short_circuit_reason`) and chat
volume are always captured.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.graphs.chat_state import ChatState
from app.models.usage_metric import UsageMetric
from app.repositories.usage_metric_repository import UsageMetricRepository
from app.utils.metrics import (
    bedrock_tokens_total,
    chat_latency_ms,
    chat_requests_total,
    chat_ttft_ms,
    estimated_cost_usd_total,
)
from app.utils.pricing import estimate_cost_usd

logger = logging.getLogger(__name__)


async def log_chat_metrics(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    session: AsyncSession = config["configurable"]["session"]

    started = state.get("started_monotonic")
    latency_ms = int((time.monotonic() - started) * 1000) if started is not None else None

    persona = state.get("persona", "user")
    endpoint = "/api/chat" if persona == "user" else "/api/opr/chat"

    # docs/19-cost-management.md §2: rate against whichever model was actually invoked
    # this turn. `text_model_used` takes priority — it's the node that also produced
    # `input_tokens`/`output_tokens` (docs/IMPLEMENTATION_PLAN.md Phase 9 notes: these
    # approximate the text-generation call only, not a separate embedding-call token
    # count). Falls back to `embedding_model_used` for the `low_similarity` tier (which
    # invokes `embed_question` but never reaches generation) — `input_tokens`/
    # `output_tokens` are `None` in that case, so this correctly resolves to `$0`, not a
    # true embedding-call cost (a known, pre-existing gap — embedding-call tokens are not
    # tracked in `ChatState` at all yet).
    model_id = state.get("text_model_used") or state.get("embedding_model_used")
    estimated_cost_usd = estimate_cost_usd(
        model_id=model_id,
        input_tokens=state.get("input_tokens"),
        output_tokens=state.get("output_tokens"),
    )

    await UsageMetricRepository(session).create(
        UsageMetric(
            session_id=state.get("session_id"),
            user_id=state.get("user_id"),
            persona=persona,
            endpoint=endpoint,
            question=state.get("original_question") or state.get("question"),
            short_circuited=state.get("short_circuited", False),
            short_circuit_reason=state.get("short_circuit_reason"),
            similarity_best_score=state.get("best_score"),
            model_embedding_used=state.get("embedding_model_used"),
            model_text_used=state.get("text_model_used"),
            input_tokens=state.get("input_tokens"),
            output_tokens=state.get("output_tokens"),
            estimated_cost_usd=estimated_cost_usd,
            latency_ms=latency_ms,
            ttft_ms=state.get("ttft_ms"),
        )
    )

    short_circuit_reason = state.get("short_circuit_reason") or "none"
    chat_requests_total.labels(persona=persona, short_circuit_reason=short_circuit_reason).inc()
    if latency_ms is not None:
        chat_latency_ms.labels(
            endpoint=endpoint, short_circuited=str(state.get("short_circuited", False))
        ).observe(latency_ms)
    ttft_ms = state.get("ttft_ms")
    if ttft_ms is not None:
        chat_ttft_ms.labels(endpoint=endpoint).observe(ttft_ms)
    input_tokens = state.get("input_tokens")
    output_tokens = state.get("output_tokens")
    if input_tokens:
        bedrock_tokens_total.labels(direction="input").inc(input_tokens)
    if output_tokens:
        bedrock_tokens_total.labels(direction="output").inc(output_tokens)
    if estimated_cost_usd:
        estimated_cost_usd_total.inc(estimated_cost_usd)

    logger.info(
        "chat_request_finished",
        extra={
            "session_id": str(state.get("session_id")),
            "persona": persona,
            "short_circuited": state.get("short_circuited", False),
            "short_circuit_reason": state.get("short_circuit_reason"),
            "latency_ms": latency_ms,
            "ttft_ms": ttft_ms,
        },
    )
    return {}
