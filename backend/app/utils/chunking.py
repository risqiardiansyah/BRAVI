"""Token-based chunking — docs/18-rag-design.md §3, docs/05-ai-agent-design.md §3.3.

Chunk length is measured with a real tokenizer, not a character-count
approximation: `CHUNK_SIZE_TOKENS`/`CHUNK_OVERLAP_TOKENS` are named — and
validated (docs/23-configuration.md §4) — as *token* counts, and character/token
ratios diverge unpredictably across languages, notably Bahasa Indonesia (this
system's focus). Cohere does not ship a local/offline tokenizer for `embed-v4`,
so `tiktoken`'s `cl100k_base` is used as the documented "conservative proxy"
(docs/18-rag-design.md §3) — the exact tokenizer is an explicitly-open
implementation detail, not a fixed requirement.
"""

from __future__ import annotations

from functools import lru_cache

import tiktoken

_ENCODING_NAME = "cl100k_base"


@lru_cache(maxsize=1)
def _get_encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding(_ENCODING_NAME)


def count_tokens(text: str) -> int:
    """Number of tokens `text` encodes to (not a character count)."""
    return len(_get_encoding().encode(text))


def split_into_token_chunks(
    text: str, *, chunk_size_tokens: int, chunk_overlap_tokens: int
) -> list[str]:
    """Split `text` into overlapping windows of at most `chunk_size_tokens` tokens
    each, so a concept split across a boundary still appears whole in at least one
    chunk (docs/05-ai-agent-design.md §3.3). Each subsequent window starts
    `chunk_size_tokens - chunk_overlap_tokens` tokens after the previous one's start.

    Returns an empty list for blank/whitespace-only input. `app/config.py` already
    enforces `chunk_overlap_tokens < chunk_size_tokens` at startup for the configured
    values; the check is repeated here defensively since this function is also
    callable directly with arbitrary arguments (e.g. from tests).
    """
    if chunk_overlap_tokens >= chunk_size_tokens:
        raise ValueError(
            "chunk_overlap_tokens must be strictly less than chunk_size_tokens "
            f"(got chunk_overlap_tokens={chunk_overlap_tokens}, "
            f"chunk_size_tokens={chunk_size_tokens})."
        )
    if not text.strip():
        return []

    encoding = _get_encoding()
    tokens = encoding.encode(text)
    stride = chunk_size_tokens - chunk_overlap_tokens

    chunks: list[str] = []
    start = 0
    total = len(tokens)
    while start < total:
        end = min(start + chunk_size_tokens, total)
        piece = encoding.decode(tokens[start:end])
        if piece.strip():
            chunks.append(piece)
        if end == total:
            break
        start += stride
    return chunks
