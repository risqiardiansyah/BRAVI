"""`generate_answer` node — docs/05-ai-agent-design.md §2.2/§2.3.

Only reached after every short-circuit tier passes. Streams tokens to the API layer via
LangGraph's custom stream mode (`get_stream_writer`) as they're produced by
`clients/bedrock_client.py` — never buffered server-side before the caller sees them
(docs/11-coding-standard.md §7). `input_tokens`/`output_tokens` are approximated with the
same tokenizer `utils/chunking.py` already uses for chunk sizing (Bedrock's Converse API
usage metadata isn't surfaced by `bedrock_client.generate_stream`'s current contract, and
extending that contract is out of this phase's scope — see `docs/IMPLEMENTATION_PLAN.md`
Phase 9 completion notes).

Shared by both graphs (docs/05-ai-agent-design.md §2.2's diagram: `operator_chat_graph`'s
`route_by_intent` routes here on its "qa" branch, same as `user_chat_graph`'s only path).
`mode` in the terminal `done` event is `"qa"` on `/api/opr/chat`'s QA path and always
`null` on `/api/chat` (docs/06-api-specification.md §0: "always null on /api/chat") — set
here from `state["persona"]` rather than duplicating this node per graph.
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.config import get_stream_writer

from app.clients.bedrock_client import (
    PromptContentBlock,
    PromptMessage,
    PromptPayload,
    bedrock_client,
)
from app.config import settings
from app.graphs.chat_state import ChatState
from app.graphs.prompts import build_qa_system_prompt
from app.utils.chunking import count_tokens


async def generate_answer(state: ChatState) -> dict[str, Any]:
    writer = get_stream_writer()

    system_prompt = build_qa_system_prompt(
        top_matches=state.get("top_matches", []),
        history_summary=state.get("history_summary"),
    )
    prompt = PromptPayload(
        system=system_prompt,
        messages=[PromptMessage(role="user", content=[PromptContentBlock(text=state["question"])])],
    )

    started = state.get("started_monotonic")
    ttft_ms: int | None = None
    chunks: list[str] = []
    async for token in bedrock_client.generate_stream(prompt):
        if ttft_ms is None and started is not None:
            ttft_ms = int((time.monotonic() - started) * 1000)
        chunks.append(token)
        writer({"type": "token", "content": token})
    answer = "".join(chunks)

    return {
        "answer": answer,
        "mode": "qa" if state.get("persona") == "operator" else None,
        "text_model_used": settings.BEDROCK_TEXT_MODEL,
        "input_tokens": count_tokens(system_prompt) + count_tokens(state["question"]),
        "output_tokens": count_tokens(answer),
        "ttft_ms": ttft_ms,
    }
