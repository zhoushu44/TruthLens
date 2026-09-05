"""
Analytics API endpoints.

GET /api/v1/analytics/overview
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.engine import get_db_session
from app.db.models import AnalysisResult, ClaimAnalysis, EvidenceItem, Message
from app.models.analytics import (
    AnalyticsApiKeyStat,
    AnalyticsModelStat,
    AnalyticsOverviewResponse,
    AnalyticsProviderStat,
    AnalyticsSummary,
    AnalyticsTimelinePoint,
)

router = APIRouter()

HALLUCINATION_VERDICTS = (
    "CONTRADICTED",
    "UNVERIFIED",
    "UNVERIFIABLE_SOURCE",
)


def _to_model_name(model_id: str) -> str:
    settings = get_settings()
    model_info = settings.supported_models.get(model_id)
    if model_info and model_info.get("name"):
        return model_info["name"]

    normalized = model_id.replace("_", " ").replace("-", " ").strip()
    return " ".join(part.capitalize() for part in normalized.split())


@router.get("/analytics/overview", response_model=AnalyticsOverviewResponse)
async def get_analytics_overview(
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db_session),
):
    """Return aggregate analytics for dashboard visualizations."""
    now = datetime.now(timezone.utc)
    start_day = now.date() - timedelta(days=days - 1)
    start_at = datetime(start_day.year, start_day.month, start_day.day, tzinfo=timezone.utc)

    confidence_expr = func.coalesce(
        ClaimAnalysis.confidence,
        1 - (ClaimAnalysis.risk_score / 100.0),
    )

    total_analyses_stmt = select(func.count(AnalysisResult.id)).where(
        AnalysisResult.created_at >= start_at
    )
    total_analyses = int((await db.execute(total_analyses_stmt)).scalar() or 0)

    claim_summary_stmt = (
        select(
            func.count(ClaimAnalysis.id).label("total_claims"),
            func.coalesce(
                func.sum(
                    case(
                        (ClaimAnalysis.verdict.in_(HALLUCINATION_VERDICTS), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("total_hallucinations"),
            func.coalesce(func.avg(confidence_expr), 0.0).label("avg_confidence"),
        )
        .join(AnalysisResult, ClaimAnalysis.analysis_id == AnalysisResult.id)
        .where(AnalysisResult.created_at >= start_at)
    )
    claim_summary = (await db.execute(claim_summary_stmt)).one()

    # 模型归属：优先取消息上的 model_id，没有消息的独立检测行取自身的 model_id
    model_id_expr = func.coalesce(Message.model_id, AnalysisResult.model_id)

    model_core_stmt = (
        select(
            model_id_expr.label("model_id"),
            func.count(func.distinct(AnalysisResult.id)).label("analyses"),
            func.count(ClaimAnalysis.id).label("claims"),
            func.coalesce(
                func.sum(
                    case(
                        (ClaimAnalysis.verdict.in_(HALLUCINATION_VERDICTS), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("hallucinations"),
            func.coalesce(func.avg(confidence_expr), 0.0).label("avg_confidence"),
        )
        .select_from(AnalysisResult)
        .outerjoin(Message, Message.analysis_result_id == AnalysisResult.id)
        .outerjoin(ClaimAnalysis, ClaimAnalysis.analysis_id == AnalysisResult.id)
        .where(
            AnalysisResult.created_at >= start_at,
            model_id_expr.isnot(None),
        )
        .group_by(model_id_expr)
    )
    model_core_rows = (await db.execute(model_core_stmt)).all()

    model_sources_stmt = (
        select(
            model_id_expr.label("model_id"),
            func.count(EvidenceItem.id).label("sources"),
        )
        .select_from(AnalysisResult)
        .outerjoin(Message, Message.analysis_result_id == AnalysisResult.id)
        .join(ClaimAnalysis, ClaimAnalysis.analysis_id == AnalysisResult.id)
        .join(EvidenceItem, EvidenceItem.claim_analysis_id == ClaimAnalysis.id)
        .where(
            AnalysisResult.created_at >= start_at,
            model_id_expr.isnot(None),
        )
        .group_by(model_id_expr)
    )
    model_source_rows = (await db.execute(model_sources_stmt)).all()
    source_map = {
        str(row.model_id): int(row.sources or 0)
        for row in model_source_rows
        if row.model_id
    }

    models = [
        AnalyticsModelStat(
            id=str(row.model_id),
            name=_to_model_name(str(row.model_id)),
            confidence=float(row.avg_confidence or 0.0),
            hallucinations=int(row.hallucinations or 0),
            sources=source_map.get(str(row.model_id), 0),
            analyses=int(row.analyses or 0),
            claims=int(row.claims or 0),
        )
        for row in model_core_rows
        if row.model_id
    ]
    models.sort(key=lambda item: (item.analyses, item.hallucinations), reverse=True)

    day_bucket = func.date_trunc("day", AnalysisResult.created_at).label("day_bucket")

    timeline_stmt = (
        select(
            day_bucket,
            func.coalesce(
                func.sum(
                    case(
                        (ClaimAnalysis.verdict.in_(HALLUCINATION_VERDICTS), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("hallucinations"),
            func.coalesce(func.avg(confidence_expr), 0.0).label("avg_confidence"),
        )
        .outerjoin(ClaimAnalysis, ClaimAnalysis.analysis_id == AnalysisResult.id)
        .where(AnalysisResult.created_at >= start_at)
        .group_by(day_bucket)
        .order_by(day_bucket)
    )
    timeline_rows = (await db.execute(timeline_stmt)).all()

    timeline_map: dict[str, tuple[int, float]] = {}
    for row in timeline_rows:
        if not row.day_bucket:
            continue
        day_key = row.day_bucket.date().isoformat()
        timeline_map[day_key] = (
            int(row.hallucinations or 0),
            float(row.avg_confidence or 0.0),
        )

    timeline: list[AnalyticsTimelinePoint] = []
    for day_offset in range(days):
        day = start_day + timedelta(days=day_offset)
        day_key = day.isoformat()
        hallucinations, confidence = timeline_map.get(day_key, (0, 0.0))
        timeline.append(
            AnalyticsTimelinePoint(
                date=day_key,
                label=day.strftime("%a"),
                hallucinations=hallucinations,
                confidence=confidence,
            )
        )

    # 每个接入 Key 的调用次数（累计，来自 api_keys 表的 usage_count）
    # 容错：表不存在或查询失败时返回空列表，不影响主统计
    api_keys: list[AnalyticsApiKeyStat] = []
    try:
        from app.db.models import ApiKey

        api_key_rows = (
            (await db.execute(select(ApiKey).order_by(ApiKey.usage_count.desc())))
            .scalars()
            .all()
        )
        api_keys = [
            AnalyticsApiKeyStat(
                id=str(k.id),
                name=k.name or "",
                prefix=k.prefix or "",
                is_active=bool(k.is_active),
                usage_count=int(k.usage_count or 0),
                last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            )
            for k in api_key_rows
        ]
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        api_keys = []

    # 各上游服务 Key 的调用次数（累计，来自 provider_usage 表）
    # 容错：表不存在或查询失败时返回空列表，不影响主统计
    providers: list[AnalyticsProviderStat] = []
    try:
        from app.config import get_settings
        from app.db.models import ProviderUsage

        settings = get_settings()
        provider_rows = (
            (await db.execute(select(ProviderUsage).order_by(ProviderUsage.calls.desc())))
            .scalars()
            .all()
        )
        providers = [
            AnalyticsProviderStat(
                id=str(p.provider),
                # zen 显示用户在设置里配的自定义模型名（如 mimo-v2.5）
                label=settings.zen_model if str(p.provider) == "zen" and settings.zen_model else None,
                calls=int(p.calls or 0),
                last_used_at=p.last_used_at.isoformat() if p.last_used_at else None,
            )
            for p in provider_rows
        ]
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        providers = []

    return AnalyticsOverviewResponse(
        days=days,
        generated_at=now,
        summary=AnalyticsSummary(
            total_analyses=total_analyses,
            total_claims=int(claim_summary.total_claims or 0),
            total_hallucinations=int(claim_summary.total_hallucinations or 0),
            average_confidence=float(claim_summary.avg_confidence or 0.0),
        ),
        models=models,
        timeline=timeline,
        api_keys=api_keys,
        providers=providers,
    )
