"""Response schemas for `GET /api/trending`/`GET /api/opr/analytics` —
docs/06-api-specification.md §4/§8, docs/02-functional-requirements.md FR-4/FR-9.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class TrendingQuestionItem(BaseModel):
    question: str
    count: int


class TrendingResponse(BaseModel):
    window_days: int
    trending: list[TrendingQuestionItem]


class AnalyticsPeriod(BaseModel):
    from_: date = Field(alias="from")
    to: date

    model_config = {"populate_by_name": True}


class TopQuestions(BaseModel):
    user: list[TrendingQuestionItem]


class VolumeByDayItem(BaseModel):
    date: date
    count: int


class Volume(BaseModel):
    total_chats: int
    by_day: list[VolumeByDayItem]


class Latency(BaseModel):
    p50_ms: float | None
    p95_ms: float | None


class ModelUsage(BaseModel):
    embedding_calls: int
    text_generation_calls: int
    short_circuited_pct: float


class AnalyticsResponse(BaseModel):
    period: AnalyticsPeriod
    top_questions: TopQuestions
    volume: Volume
    latency: Latency
    model_usage: ModelUsage
    estimated_cost_usd: float
