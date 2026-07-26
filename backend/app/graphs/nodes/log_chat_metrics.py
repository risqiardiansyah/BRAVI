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

logger = logging.getLogger(__name__)


async def log_chat_metrics(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    session: AsyncSession = config["configurable"]["session"]

    started = state.get("started_monotonic")
    latency_ms = int((time.monotonic() - started) * 1000) if started is not None else None

    persona = state.get("persona", "user")
    endpoint = "/api/chat" if persona == "user" else "/api/opr/chat"

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
            latency_ms=latency_ms,
        )
    )

    logger.info(
        "chat_request_finished",
        extra={
            "session_id": str(state.get("session_id")),
            "persona": persona,
            "short_circuited": state.get("short_circuited", False),
            "short_circuit_reason": state.get("short_circuit_reason"),
            "latency_ms": latency_ms,
        },
    )
    return {}
