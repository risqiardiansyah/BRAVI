"""`append_sources` node — docs/05-ai-agent-design.md §2.2/§2.3, docs/06-api-specification.md §0.

Appends a `## Sources` section to the generated answer using `[Link Text](URL)` per
citation, built from `top_matches` — the model itself is instructed (docs/prompts/ai-agent.md
§1) never to fabricate this section. A match with no `source_url` is cited by title only,
without a link (docs/07-database-design.md §3.4). Also builds the structured `sources`
array the `done` SSE event exposes verbatim (docs/06-api-specification.md §0).
"""

from __future__ import annotations

from typing import Any

from app.graphs.chat_state import ChatState, SourceItem

_UNTITLED_DOCUMENT = "Dokumen tanpa judul"


async def append_sources(state: ChatState) -> dict[str, Any]:
    top_matches = state.get("top_matches") or []
    answer = state.get("answer") or ""

    if not top_matches:
        return {"answer": answer, "sources": []}

    lines = ["## Sources"]
    sources: list[SourceItem] = []
    for match in top_matches:
        title = match.get("title") or _UNTITLED_DOCUMENT
        url = match.get("source_url")
        lines.append(f"- [{title}]({url})" if url else f"- {title}")
        sources.append(
            {
                "document_id": match["document_id"],
                "title": title,
                "url": url,
                "page": match.get("page_number"),
                "valid_until": match.get("valid_until"),
                "superseded_by_title": match.get("superseded_by_title"),
            }
        )

    full_answer = f"{answer}\n\n" + "\n".join(lines)
    return {"answer": full_answer, "sources": sources}
