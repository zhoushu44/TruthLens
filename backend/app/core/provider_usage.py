"""上游 Provider 调用计数（Groq / Tavily 等服务 Key 的实际用量）。

用法：
    from app.core.provider_usage import record_provider_call
    await record_provider_call("groq")   # 真实出站调用成功后记一次

设计：
- 成功才记（能真实反映各服务 Key 的消耗量，比如 Groq 免费额度）。
- 自带 DB 会话，调用方无需传 session。
- 永远不抛异常：计数失败只打 warning，绝不影响主流程。
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)

# 仪表盘展示的已知服务（即使 0 次也展示，方便确认配置是否生效）
KNOWN_PROVIDERS = (
    "groq",
    "nvidia",
    "openrouter",
    "zen",
    "gemini",
    "tavily",
    "serper",
    "factcheck",
)


async def record_provider_call(provider: str) -> None:
    """记录一次上游服务调用（upsert：calls + 1）。失败时静默跳过。"""
    if not provider:
        return
    try:
        from app.db.engine import engine

        now = datetime.now(timezone.utc)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO provider_usage (provider, calls, last_used_at) "
                    "VALUES (:p, 1, :now) "
                    "ON CONFLICT (provider) DO UPDATE SET "
                    "calls = provider_usage.calls + 1, "
                    "last_used_at = :now"
                ),
                {"p": provider, "now": now},
            )
    except Exception as e:
        logger.warning(f"provider_usage record skipped ({provider}): {e}")
