"""Table-driven tests for the greeting/out-of-topic short-circuit classifiers —
docs/12-testing-strategy.md §2 ("Short-circuit classifiers: ... table-driven tests with
representative positive/negative examples").
"""

from __future__ import annotations

import pytest

from app.graphs.canned_responses import is_greeting, is_out_of_topic


@pytest.mark.parametrize(
    "question",
    [
        "Halo",
        "halo!",
        "Hi",
        "hello",
        "Selamat pagi",
        "selamat siang",
        "Apa kabar?",
        "Terima kasih",
        "makasih",
    ],
)
def test_is_greeting_positive_examples(question: str) -> None:
    assert is_greeting(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "Apa syarat pengajuan klaim asuransi kesehatan?",
        "Halo, apa syarat pengajuan klaim?",
        "What are the requirements to file a claim?",
        "Terima kasih atas bantuannya, tapi saya masih ada pertanyaan lain",
        "",
    ],
)
def test_is_greeting_negative_examples(question: str) -> None:
    assert is_greeting(question) is False


@pytest.mark.parametrize(
    "question",
    [
        "Tolong buatkan puisi tentang cinta",
        "Ceritakan lelucon dong",
        "write me a poem about the sea",
        "tell me a joke",
        "Siapa presiden Indonesia?",
        "1 + 1 berapa?",
    ],
)
def test_is_out_of_topic_positive_examples(question: str) -> None:
    assert is_out_of_topic(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "Apa syarat pengajuan klaim asuransi kesehatan?",
        "What are the requirements to file a health insurance claim?",
        "Bagaimana cara mengajukan komplain ke customer service?",
    ],
)
def test_is_out_of_topic_negative_examples(question: str) -> None:
    assert is_out_of_topic(question) is False
