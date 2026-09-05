"""OpenAI 兼容接口（让别的系统零改代码就能调 TruthLens 的 chat 能力）。

- GET /v1/models：返回 OpenAI 格式模型列表（从 supported_models 取有 Key 的）
- POST /v1/chat/completions：OpenAI 请求格式，支持 stream=true/false
  body: {model, messages:[{role,content}], stream}
  model 既可以是 TruthLens 的 model_id（如 openai/gpt-oss-120b），
  也可以只写 provider 简写，服务端自动映射到该 provider 第一个可用模型。

鉴权：走可选鉴权（有 Key 计数，没 Key 放行，API_KEY_REQUIRED=True 才强制）。
"""
import time
import uuid
import logging
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import get_settings
from app.core.apikey_auth import require_api_key_if_configured
from app.utils.llm_clients import get_llm_client

logger = logging.getLogger(__name__)
router = APIRouter()


class OAIChatMessage(BaseModel):
    role: str = "user"
    content: Any = ""


class OAIChatRequest(BaseModel):
    model: str = ""
    messages: list[OAIChatMessage] = []
    stream: bool = False


def _available_models() -> list[dict]:
    s = get_settings()
    out = []
    for model_id, info in s.supported_models.items():
        field = info.get("api_key_field")
        if field and not getattr(s, field, None):
            continue
        out.append({"id": model_id, "name": info.get("name", model_id)})
    return out


def _resolve_model(requested: str) -> str:
    s = get_settings()
    if requested in s.supported_models:
        return requested
    avail = _available_models()
    if not avail:
        raise HTTPException(status_code=400, detail="服务端暂无可用模型，请先在 .env 配置 GROQ/ZEN 等 Key")
    # 简写映射：groq/nvidia/openrouter/zen/gemini -> 该 provider 第一个可用
    low = (requested or "").lower()
    for m in avail:
        info = s.supported_models[m["id"]]
        if low and low in str(info.get("provider", "")).lower():
            return m["id"]
    # 默认第一个
    return avail[0]["id"]


def _to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and "text" in p:
                t = p["text"]
                parts.append(t if isinstance(t, str) else str(t))
            elif isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text", "")))
        return "\n".join(parts)
    return str(content or "")


@router.get("/models")
async def oai_models(_: Optional[dict] = Depends(require_api_key_if_configured)):
    data = [
        {"id": m["id"], "object": "model", "created": int(time.time()), "owned_by": "truthlens"}
        for m in _available_models()
    ]
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def oai_chat(
    req: OAIChatRequest, _: Optional[dict] = Depends(require_api_key_if_configured)
):
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")
    model_id = _resolve_model(req.model)
    msgs = [{"role": m.role, "content": _to_text(m.content)} for m in req.messages]
    last = msgs[-1]
    history = msgs[:-1]
    message = last["content"] if last["role"] == "user" else "\n".join(m["content"] for m in msgs)

    client = get_llm_client()
    if req.stream:
        async def gen():
            cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            created = int(time.time())
            try:
                async for chunk in client.chat_stream(
                    model_id=model_id, message=message,
                    conversation_history=history,
                ):
                    import json as _json
                    payload = {
                        "id": cid, "object": "chat.completion.chunk",
                        "created": created, "model": model_id,
                        "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
                    }
                    yield f"data: {_json.dumps(payload, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                logger.error(f"oai stream failed: {e}", exc_info=True)
                yield f"data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    try:
        text = await client.chat(
            model_id=model_id, message=message, conversation_history=history
        )
    except Exception as e:
        logger.error(f"oai chat failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {},
    }
