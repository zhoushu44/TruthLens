"""API Key 鉴权（自己用 + 外部调用）。

规则（按你的要求：自己用、开放、不设限、不限流）：
- 支持两种 Key：.env 主 Key（TRUTHLENS_API_KEY）+ DB 表里创建的子 Key
- 格式统一 tl- + 32 位随机
- 存储只存 sha256，不存明文
- 兼容模式：API_KEY_REQUIRED=False 时，有 Key 就校验计数，没 Key 也放行，
  保证现有 WebUI / 浏览器插件不断；公网部署后可把 API_KEY_REQUIRED=True 切强制
- 不限流：只做 usage_count / last_used_at 计数
"""
import hashlib
import secrets
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import Header, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.engine import get_db_session

logger = logging.getLogger(__name__)


def generate_raw_key() -> str:
    return "tl-" + secrets.token_urlsafe(24)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def key_prefix(raw: str) -> str:
    return raw[:10] if len(raw) >= 10 else raw


async def _lookup_db_key(db: AsyncSession, raw: str):
    from app.db.models import ApiKey
    h = hash_key(raw)
    res = await db.execute(select(ApiKey).where(ApiKey.key_hash == h))
    return res.scalars().first()


def _check_expiry(expires_at) -> bool:
    if not expires_at:
        return True
    now = datetime.now(timezone.utc)
    exp = expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp > now


async def verify_api_key_optional(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> Optional[dict]:
    """可选鉴权：没带 Key 放行（兼容 WebUI/插件），带了就校验 + 计数。"""
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    elif x_api_key:
        raw = x_api_key.strip()
    # 前端 localStorage 也可能走 query？不建议，这里只认 header
    if not raw:
        return None

    settings = get_settings()
    # 1) 主 Key（.env）
    if settings.truthlens_api_key and raw == settings.truthlens_api_key:
        return {"type": "master", "name": "master (.env)", "prefix": key_prefix(raw)}

    # 2) DB 子 Key
    try:
        record = await _lookup_db_key(db, raw)
    except Exception as e:
        logger.warning(f"API key DB lookup failed: {e}")
        return None
    if not record or not record.is_active or not _check_expiry(record.expires_at):
        return None
    # 计数（不限流，只记录）
    try:
        record.usage_count = (record.usage_count or 0) + 1
        record.last_used_at = datetime.now(timezone.utc)
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
    return {"type": "db", "name": record.name, "prefix": record.prefix, "id": record.id}


async def require_api_key_if_configured(
    key_info: Optional[dict] = Depends(verify_api_key_optional),
) -> Optional[dict]:
    """API_KEY_REQUIRED=True 时强制要求有效 Key，否则 401。"""
    settings = get_settings()
    if settings.api_key_required and key_info is None:
        raise HTTPException(
            status_code=401,
            detail="缺少或无效的 API Key，请在请求头带 Authorization: Bearer tl-xxx",
        )
    return key_info
