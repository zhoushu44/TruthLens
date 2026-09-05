"""API Key 管理接口（WebUI 的 API Key 页用）。

- GET /api/v1/apikeys/status：服务器地址、端口、主 Key 是否配置、是否强制鉴权
- GET /api/v1/apikeys：列出 DB 子 Key（不返回明文）
- POST /api/v1/apikeys：创建，body {name}，明文只返回一次
- DELETE /api/v1/apikeys/{key_id}：吊销（软删除 is_active=False）
自己用：管理页不设鉴权，靠内网/服务器防火墙隔离；外部调用 detect/chat 才校验 Key。
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.apikey_auth import generate_raw_key, hash_key, key_prefix
from app.db.engine import get_db_session
from app.db.models import ApiKey

logger = logging.getLogger(__name__)
router = APIRouter()


class CreateKeyRequest(BaseModel):
    name: str = "我的调用 Key"


@router.get("/apikeys/status")
async def apikey_status():
    s = get_settings()
    master_set = bool(s.truthlens_api_key)
    return {
        "server_port": s.port,
        "master_configured": master_set,
        "master_prefix": key_prefix(s.truthlens_api_key) if master_set else None,
        "api_key_required": s.api_key_required,
        "auth_header": "Authorization: Bearer tl-xxx（或 X-API-Key: tl-xxx）",
        "detect_url": "/api/v1/detect",
        "openai_chat_url": "/v1/chat/completions",
        "openai_models_url": "/v1/models",
        "swagger_url": "/docs",
    }


@router.get("/apikeys")
async def list_keys(db: AsyncSession = Depends(get_db_session)):
    res = await db.execute(select(ApiKey).order_by(desc(ApiKey.created_at)))
    items = res.scalars().all()
    return {
        "keys": [
            {
                "id": k.id,
                "name": k.name,
                "prefix": k.prefix,
                "is_active": k.is_active,
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                "usage_count": k.usage_count or 0,
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                "created_at": k.created_at.isoformat() if k.created_at else None,
            }
            for k in items
        ]
    }


@router.post("/apikeys")
async def create_key(body: CreateKeyRequest, db: AsyncSession = Depends(get_db_session)):
    raw = generate_raw_key()
    record = ApiKey(
        name=(body.name or "我的调用 Key")[:200],
        prefix=key_prefix(raw),
        key_hash=hash_key(raw),
        is_active=True,
        expires_at=None,
        usage_count=0,
        last_used_at=None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return {
        "id": record.id,
        "name": record.name,
        "prefix": record.prefix,
        # 明文只给这一次，前端要弹窗让用户复制
        "api_key": raw,
        "tip": "明文只显示一次，请立即复制保存",
    }


@router.delete("/apikeys/{key_id}")
async def revoke_key(key_id: str, db: AsyncSession = Depends(get_db_session)):
    res = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    record = res.scalars().first()
    if not record:
        return {"ok": False, "detail": "Key 不存在"}
    record.is_active = False
    await db.commit()
    return {"ok": True, "id": key_id}
