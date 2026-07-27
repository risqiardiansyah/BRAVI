"""Canned short-circuit response text + classifier keyword lists —
docs/05-ai-agent-design.md §2.5, docs/prompts/ai-agent.md §3-§5.

Canonical response text is transcribed verbatim (authored directly in Bahasa Indonesia,
never translated at runtime). Centralized here as plain constants rather than a DB-backed
config table: `05-ai-agent-design.md` §2.5 suggests "e.g. a responses config table" as one
option, but no such table exists in `07-database-design.md` §3 and this phase does not
introduce one (inventing a new schema for it is out of this phase's documented scope) — a
future phase can move these to a live-editable store without changing any node's logic,
since every node already reads through this one module.

Classifier keyword lists (`_GREETING_PHRASES`/`_OUT_OF_TOPIC_PATTERNS`) are likewise plain
constants for the same reason, tunable in one place without touching node logic.
"""

from __future__ import annotations

import re
import unicodedata

# --- docs/prompts/ai-agent.md §3 — Greeting / Small-talk (no LLM call) ------------------

GREETING_RESPONSE = (
    "Halo! Saya Bravi AI Chatbot. Saya dapat membantu menjawab pertanyaan berdasarkan basis\n"
    "pengetahuan kami — ada yang ingin Anda tanyakan?"
)

# --- docs/prompts/ai-agent.md §4 — Out-of-Topic (no LLM call) ---------------------------

OUT_OF_TOPIC_RESPONSE = (
    "Saya hanya dapat membantu menjawab pertanyaan yang berkaitan dengan isi basis pengetahuan\n"
    "kami. Bisakah Anda mengajukan pertanyaan yang lebih relevan dengan topik tersebut?"
)

# --- docs/prompts/ai-agent.md §5 — No-Relevant-Knowledge-Found (no LLM call) ------------

NO_KNOWLEDGE_FOUND_RESPONSE = (
    "Saya belum menemukan informasi yang relevan mengenai hal tersebut di basis pengetahuan kami.\n"
    "Anda bisa mencoba mengajukan pertanyaan dengan cara lain, atau mungkin topik ini perlu\n"
    "ditambahkan oleh operator ke basis pengetahuan."
)


def _normalize(text: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace — for exact-phrase
    matching that's tolerant of casing/punctuation but not of substring false-positives
    (docs/05-ai-agent-design.md §2.3: greeting/out-of-topic classifiers "must be near-zero
    latency, zero LLM cost")."""
    stripped = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    stripped = re.sub(r"[^\w\s]", " ", stripped).strip().lower()
    return re.sub(r"\s+", " ", stripped)


# Exact-match (post-normalization) greeting/small-talk phrases — bilingual, matching
# docs/05-ai-agent-design.md §2.3's "rule-based (regex/keyword list)" guidance. Exact-match
# (rather than substring search) deliberately avoids misclassifying a real question that
# happens to start with a greeting word (e.g. "Halo, apa syarat pengajuan klaim?").
_GREETING_PHRASES = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "halo",
        "hai",
        "hallo",
        "helo",
        "selamat pagi",
        "selamat siang",
        "selamat sore",
        "selamat malam",
        "apa kabar",
        "how are you",
        "assalamualaikum",
        "terima kasih",
        "thanks",
        "thank you",
        "makasih",
        "terimakasih",
    }
)


def is_greeting(question: str) -> bool:
    return _normalize(question) in _GREETING_PHRASES


# Cheap keyword/pattern denylist for obviously off-topic request categories (creative
# writing, jokes, generic trivia/homework unrelated to a document knowledge base) —
# positioned before `embed_question` per the non-negotiable short-circuit ordering
# (docs/IMPLEMENTATION_PLAN.md §3, docs/05-ai-agent-design.md §2.4). §2.4 explicitly
# permits either a pure heuristic here or merging with the embedding call; since the
# embedding call must not happen before this check (fixed node order in §2.2's diagram),
# a heuristic is used. Anything not caught here still passes through the
# `check_similarity_threshold` gate as a defense-in-depth net for off-topic questions
# that slip past this keyword list.
_OUT_OF_TOPIC_PATTERNS = (
    r"\bbuatkan(lah)?\s+(saya\s+)?(sebuah\s+)?(puisi|pantun|cerita|lagu|lelucon)\b",
    r"\bceritakan\s+(sebuah\s+)?lelucon\b",
    r"\bwrite\s+(me\s+)?(a|an)\s+(poem|song|story|joke)\b",
    r"\btell\s+me\s+a\s+joke\b",
    r"\bsiapa\s+presiden\b",
    r"\bwho\s+is\s+the\s+president\b",
    r"\bcuaca\s+(hari ini|besok|di)\b",
    r"\bwhat(?:'s| is)\s+the\s+weather\b",
    r"\bskor\s+(pertandingan|bola|sepak bola)\b",
    r"\b\d+\s*[\+\-\*/]\s*\d+\b",
)
_OUT_OF_TOPIC_RE = re.compile("|".join(_OUT_OF_TOPIC_PATTERNS), re.IGNORECASE)


def is_out_of_topic(question: str) -> bool:
    return bool(_OUT_OF_TOPIC_RE.search(question))


# --- docs/prompts/ai-agent.md §6 — Add-Knowledge-Intent (Operator only, no LLM call) ----

# The one deliberate exception to "answers are plain Markdown": `<BTN>Label</BTN>` is a
# custom, non-standard inline tag the frontend parses into an actionable button — not
# CommonMark, never rendered as literal HTML by the client (docs/05-ai-agent-design.md
# §2.5, docs/06-api-specification.md §0/§5).
ADD_KNOWLEDGE_INTENT_RESPONSE = (
    "Silahkan klik tombol berikut untuk mengisi form: <BTN>Add Knowledge</BTN>"
)

# Bilingual keyword/phrase match — docs/05-ai-agent-design.md §2.3's own examples:
# "tambah knowledge ai", "tambah pengetahuan ai", "add ai knowledge", "add knowledge ai".
# `operator_chat_graph` only; this node/classifier must never be wired into
# `user_chat_graph` (docs/11-coding-standard.md §8.1).
_ADD_KNOWLEDGE_INTENT_PATTERNS = (
    r"\btambah\s+(knowledge|pengetahuan)\s+ai\b",
    r"\badd\s+ai\s+knowledge\b",
    r"\badd\s+knowledge\s+ai\b",
)
_ADD_KNOWLEDGE_INTENT_RE = re.compile("|".join(_ADD_KNOWLEDGE_INTENT_PATTERNS), re.IGNORECASE)


def is_add_knowledge_intent(question: str) -> bool:
    return bool(_ADD_KNOWLEDGE_INTENT_RE.search(question))
