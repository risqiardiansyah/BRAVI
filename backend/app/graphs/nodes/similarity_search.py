"""`similarity_search` node — docs/05-ai-agent-design.md §2.2/§2.3, docs/18-rag-design.md §4.

`RETRIEVAL_TOP_K` chunks for the QA path (this phase). The Operator summary sub-flow's
`SUMMARY_TOP_K` re-query happens inside `generate_summary` itself (Phase 10) — see
docs/05-ai-agent-design.md §2.3's note that the re-query only happens "once route_by_intent
selects the summary sub-flow", which does not exist in `user_chat_graph`.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.graphs.chat_state import ChatState, TopMatch
from app.repositories.knowledge_chunk_repository import KnowledgeChunkRepository


async def similarity_search(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    session: AsyncSession = config["configurable"]["session"]
    matches = await KnowledgeChunkRepository(session).similarity_search(
        state["question_embedding"], top_k=settings.RETRIEVAL_TOP_K
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
    best_score = top_matches[0]["score"] if top_matches else None
    return {"top_matches": top_matches, "best_score": best_score}
