"""
Pydantic schemas for analytics endpoints.
"""

from datetime import datetime
from pydantic import BaseModel, Field


class AnalyticsSummary(BaseModel):
    """Top-level summary metrics for the selected time window."""

    total_analyses: int = 0
    total_claims: int = 0
    total_hallucinations: int = 0
    average_confidence: float = Field(0.0, ge=0.0, le=1.0)


class AnalyticsModelStat(BaseModel):
    """Per-model analytics metrics."""

    id: str
    name: str
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    hallucinations: int = 0
    sources: int = 0
    analyses: int = 0
    claims: int = 0


class AnalyticsTimelinePoint(BaseModel):
    """Daily trend data point for charting."""

    date: str
    label: str
    hallucinations: int = 0
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class AnalyticsApiKeyStat(BaseModel):
    """Per access-key call statistics (cumulative)."""

    id: str
    name: str
    prefix: str
    is_active: bool = True
    usage_count: int = 0
    last_used_at: str | None = None


class AnalyticsProviderStat(BaseModel):
    """Per upstream-provider call statistics (cumulative)."""

    id: str  # groq / tavily / ...
    label: str | None = None  # 展示名；zen 显示用户配置的自定义模型名
    calls: int = 0
    last_used_at: str | None = None


class AnalyticsOverviewResponse(BaseModel):
    """Response body for GET /analytics/overview."""

    days: int
    generated_at: datetime
    summary: AnalyticsSummary
    models: list[AnalyticsModelStat]
    timeline: list[AnalyticsTimelinePoint]
    api_keys: list[AnalyticsApiKeyStat] = []
    providers: list[AnalyticsProviderStat] = []
