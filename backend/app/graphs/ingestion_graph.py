"""`ingestion_graph` — docs/05-ai-agent-design.md §3.2, docs/04-system-architecture.md §3.

Used by the startup batch job (`app/jobs/run_initial_ingestion.py`) and, in a later
phase, `/api/opr/ingest`. Per-document failure isolation (docs/05-ai-agent-design.md
§3.2's "on any node failure -> mark status='failed', persist error, continue batch for
other sources") is implemented as graph-level conditional routing: any of
`load_source`/`extract_text`/`chunk_text`/`embed_chunks` setting `state["status"] =
"failed"` routes straight to `update_ingestion_status` (skipping the remaining
pipeline nodes) rather than raising — a corrupt/unreachable source therefore never
aborts the surrounding batch, matching Phase 6's Definition of Done.

One file per graph (docs/11-coding-standard.md §8); node functions live in
`graphs/nodes/` and this module only wires nodes/edges together.
"""

from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graphs.ingestion_state import IngestionState
from app.graphs.nodes.chunk_text import chunk_text
from app.graphs.nodes.embed_chunks import embed_chunks
from app.graphs.nodes.extract_text import extract_text
from app.graphs.nodes.load_source import load_source
from app.graphs.nodes.log_metrics import log_metrics
from app.graphs.nodes.store_vectors import store_vectors
from app.graphs.nodes.update_ingestion_status import update_ingestion_status


def _route_after(next_node: str) -> Callable[[IngestionState], str]:
    """Builds a routing function: on failure, jump straight to the shared terminal
    status-update node; otherwise continue to `next_node`."""

    def _router(state: IngestionState) -> str:
        return "update_ingestion_status" if state.get("status") == "failed" else next_node

    return _router


def build_ingestion_graph() -> (
    CompiledStateGraph[IngestionState, None, IngestionState, IngestionState]
):
    builder: StateGraph[IngestionState, None, IngestionState, IngestionState] = StateGraph(
        IngestionState
    )

    builder.add_node("load_source", load_source)
    builder.add_node("extract_text", extract_text)
    builder.add_node("chunk_text", chunk_text)
    builder.add_node("embed_chunks", embed_chunks)
    builder.add_node("store_vectors", store_vectors)
    builder.add_node("update_ingestion_status", update_ingestion_status)
    builder.add_node("log_metrics", log_metrics)

    builder.add_edge(START, "load_source")
    builder.add_conditional_edges(
        "load_source",
        _route_after("extract_text"),
        ["extract_text", "update_ingestion_status"],
    )
    builder.add_conditional_edges(
        "extract_text",
        _route_after("chunk_text"),
        ["chunk_text", "update_ingestion_status"],
    )
    builder.add_conditional_edges(
        "chunk_text",
        _route_after("embed_chunks"),
        ["embed_chunks", "update_ingestion_status"],
    )
    builder.add_conditional_edges(
        "embed_chunks",
        _route_after("store_vectors"),
        ["store_vectors", "update_ingestion_status"],
    )
    builder.add_edge("store_vectors", "update_ingestion_status")
    builder.add_edge("update_ingestion_status", "log_metrics")
    builder.add_edge("log_metrics", END)

    return builder.compile()


ingestion_graph = build_ingestion_graph()
