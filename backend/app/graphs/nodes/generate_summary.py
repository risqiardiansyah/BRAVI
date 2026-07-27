"""`generate_summary` node — docs/05-ai-agent-design.md §2.2/§2.3, `operator_chat_graph`
only.

Reached only when `route_by_intent` classifies the operator's question as a summarization
request. Re-queries similarity search with the broader `SUMMARY_TOP_K` (docs/05-ai-agent-
design.md §2.3: "a broader value ... once route_by_intent selects the summary sub-flow")
rather than reusing `similarity_search`'s `RETRIEVAL_TOP_K` result — that query already ran
earlier in the graph (to gate `check_similarity_threshold`), before `route_by_intent` had
a chance to classify the mode. Overwrites `top_matches` so `append_sources`/the `done`
event's `sources` array cites the broader summary-specific set, not the narrower QA one.

Streams tokens the same way `generate_answer` does (`get_stream_writer`, never buffered
server-side — docs/11-coding-standard.md §7).
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.bedrock_client import (
    PromptContentBlock,
    PromptMessage,
    PromptPayload,
    bedrock_client,
)
from app.config import settings
from app.graphs.chat_state import ChatState, TopMatch
from app.graphs.prompts import build_operator_summary_prompt
from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from app.utils.chunking import count_tokens


async def generate_summary(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    writer = get_stream_writer()
    session: AsyncSession = config["configurable"]["session"]

    matches = await KnowledgeChunkRepository(session).similarity_search(
        state["question_embedding"], top_k=settings.SUMMARY_TOP_K
    )
    top_matches: list[TopMatch] = [
        {
            "chunk_id": match.chunk_id,
            "document_id": match.document_id,
            "content": match.content,
            "score": match.score,
            "page_number": match.page_number,
            "title": match.title,
            "source_url": match.source_url,
            "valid_until": match.valid_until,
            "superseded_by_title": match.superseded_by_title,
        }
        for match in matches
    ]

    system_prompt = build_operator_summary_prompt(
        top_matches=top_matches, question=state["question"]
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
        "mode": "summary",
        "top_matches": top_matches,
        "text_model_used": settings.BEDROCK_TEXT_MODEL,
        "input_tokens": count_tokens(system_prompt) + count_tokens(state["question"]),
        "output_tokens": count_tokens(answer),
        "ttft_ms": ttft_ms,
    }
