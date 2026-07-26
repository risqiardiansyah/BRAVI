"""Unit tests for `app/utils/chunking.py` — docs/18-rag-design.md §3,
docs/05-ai-agent-design.md §3.3, docs/IMPLEMENTATION_PLAN.md Phase 6.

Chunking must be measured in *tokens*, not characters — these tests prove that
directly against Bahasa Indonesia/non-ASCII text (where character count and token
count diverge), rather than trusting a character-count approximation.
"""

from __future__ import annotations

import pytest

from app.utils.chunking import count_tokens, split_into_token_chunks

# Repeated Indonesian sentence — long enough to force multiple chunks at small
# CHUNK_SIZE_TOKENS values used below, and non-ASCII enough that a character-count
# chunker would disagree with a token-count chunker about where chunks end.
_INDONESIAN_TEXT = (
    "Kecerdasan buatan membantu perusahaan menjawab pertanyaan pelanggan dengan cepat "
    "dan akurat, tanpa mengorbankan kualitas layanan. "
) * 30


def test_char_count_and_token_count_diverge_for_indonesian_text() -> None:
    """Proves the tokenizer is actually doing tokenization, not a character-count
    stand-in — docs/18-rag-design.md §3's core requirement."""
    token_count = count_tokens(_INDONESIAN_TEXT)
    char_count = len(_INDONESIAN_TEXT)
    assert token_count != char_count
    # A real BPE tokenizer averages meaningfully fewer tokens than characters for
    # ordinary prose; this would be false if count_tokens were secretly len(text).
    assert token_count < char_count


def test_every_chunk_respects_chunk_size_tokens() -> None:
    chunks = split_into_token_chunks(
        _INDONESIAN_TEXT, chunk_size_tokens=50, chunk_overlap_tokens=10
    )
    assert len(chunks) > 1
    for chunk in chunks:
        assert count_tokens(chunk) <= 50


def test_chunks_overlap_by_approximately_the_configured_overlap() -> None:
    chunks = split_into_token_chunks(
        _INDONESIAN_TEXT, chunk_size_tokens=50, chunk_overlap_tokens=10
    )
    assert len(chunks) >= 2
    # The tail of chunk[i] and the head of chunk[i+1] should share content — proving
    # a concept split across a boundary still appears whole in at least one chunk
    # (docs/05-ai-agent-design.md §3.3's overlap rationale), not a hard, non-overlapping
    # split.
    first_words = chunks[0].split()
    second_words = chunks[1].split()
    overlap_candidates = set(first_words[-8:])
    assert overlap_candidates & set(second_words[:8])


def test_short_text_produces_a_single_chunk() -> None:
    chunks = split_into_token_chunks(
        "Halo, apa kabar?", chunk_size_tokens=700, chunk_overlap_tokens=100
    )
    assert len(chunks) == 1
    assert chunks[0].strip() == "Halo, apa kabar?"


def test_blank_text_produces_no_chunks() -> None:
    assert (
        split_into_token_chunks("   \n\t  ", chunk_size_tokens=700, chunk_overlap_tokens=100) == []
    )
    assert split_into_token_chunks("", chunk_size_tokens=700, chunk_overlap_tokens=100) == []


def test_overlap_must_be_strictly_less_than_chunk_size() -> None:
    """Mirrors the startup-validation invariant in app/config.py
    (docs/23-configuration.md §4) — asserted directly here too since this function
    is callable independently of config-loaded values."""
    with pytest.raises(ValueError):
        split_into_token_chunks("some text", chunk_size_tokens=100, chunk_overlap_tokens=100)
    with pytest.raises(ValueError):
        split_into_token_chunks("some text", chunk_size_tokens=100, chunk_overlap_tokens=150)


def test_count_tokens_matches_manual_encoding_length() -> None:
    from app.utils.chunking import _get_encoding  # noqa: PLC0415 — test-only introspection

    encoding = _get_encoding()
    text = "Selamat pagi, dunia!"
    assert count_tokens(text) == len(encoding.encode(text))
