"""
Settings API — Web可配置的 Key / URL 管理.

GET  /api/v1/settings/schema  → 前端渲染分组、必填标识、作用说明
GET  /api/v1/settings/status  → 每个Key是否已配置(脱敏)+当前生效状态
PUT  /api/v1/settings         → 保存到 backend/.env,热重载,无需重启
POST /api/v1/settings/effective → 保存后查询哪些功能已生效(声明提取/裁决/聊天模型/联网搜索)
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

# ── 设置项元数据(前端直接渲染,含必填与作用说明) ──────────────────────────

SETTINGS_DEFS = [
    # —— 对话 & 声明提取:三选一,至少填一个 ——
    {
        "key": "GROQ_API_KEY",
        "field": "groq_api_key",
        "label": "Groq Key",
        "category": "对话与声明提取",
        "required": "三选一必填",
        "required_level": "group-required",
        "description": "聊天对话(Groq模型)+声明提取默认通道。速度最快,优先使用。不填则Groq模型不可用,声明提取自动降级到 NVIDIA / OpenRouter。",
        "placeholder": "gsk_…",
        "secret": True,
    },
    {
        "key": "NVIDIA_API_KEY",
        "field": "nvidia_api_key",
        "label": "NVIDIA NIM Key",
        "category": "对话与声明提取",
        "required": "三选一必填",
        "required_level": "group-required",
        "description": "聊天对话(NVIDIA模型)+声明提取备用通道。Groq不可用时自动切换。不填则 NVIDIA 模型不可用。",
        "placeholder": "nvapi-…",
        "secret": True,
    },
    {
        "key": "OPENROUTER_API_KEY",
        "field": "openrouter_api_key",
        "label": "OpenRouter Key",
        "category": "对话与声明提取",
        "required": "三选一必填",
        "required_level": "group-required",
        "description": "聊天对话(OpenRouter免费模型)+声明提取兜底通道。三者至少填一个,否则声明提取完全不可用,检测流程只能走启发式兜底。",
        "placeholder": "sk-or-v1-…",
        "secret": True,
    },
    # —— 裁决:二选一,强烈推荐 ——
    {
        "key": "GEMINI_API_KEY",
        "field": "gemini_api_key",
        "label": "Gemini Key",
        "category": "裁决(终判)",
        "required": "二选一推荐",
        "required_level": "recommended",
        "description": "最终裁决首选(Gemini 原生 SDK)。对每条声明输出 VERIFIED / CONTRADICTED 等结论+中文理由。不填则自动用 MiMo 通道,两者都不填则降级为 NLI 分数启发式,理由质量明显下降。",
        "placeholder": "AIza…",
        "secret": True,
    },
    {
        "key": "ZEN_API_KEY",
        "field": "zen_api_key",
        "label": "MiMo Key (Zen 网关)",
        "category": "裁决(终判)",
        "required": "二选一推荐",
        "required_level": "recommended",
        "description": "最终裁决备用(OpenAI兼容网关,模型 mimo-v2.5)。Gemini不可用时自动切换。也可直接用于聊天(mimo-v2.5)。",
        "placeholder": "sk-…",
        "secret": True,
    },
    {
        "key": "ZEN_BASE_URL",
        "field": "zen_base_url",
        "label": "Zen Base URL",
        "category": "裁决(终判)",
        "required": "选填",
        "required_level": "optional",
        "description": "MiMo 网关地址。默认 https://opencode.ai/zen/go/v1,自建网关才需要改。明文保存。",
        "placeholder": "https://opencode.ai/zen/go/v1",
        "secret": False,
    },
    {
        "key": "ZEN_MODEL",
        "field": "zen_model",
        "label": "Zen Model",
        "category": "裁决(终判)",
        "required": "选填",
        "required_level": "optional",
        "description": "裁决与聊天使用的网关模型名,默认 mimo-v2.5。明文保存。",
        "placeholder": "mimo-v2.5",
        "secret": False,
    },
    # —— 联网搜索验证 ——
    {
        "key": "TAVILY_API_KEY",
        "field": "tavily_api_key",
        "label": "Tavily Key",
        "category": "联网搜索验证",
        "required": "推荐必填",
        "required_level": "recommended",
        "description": "联网验证主力,返回整页内容最适合 NLI 判定。不填则联网证据大幅减少,通用事实类声明容易判为 UNVERIFIED。",
        "placeholder": "tvly-…",
        "secret": True,
    },
    {
        "key": "SERPER_API_KEY",
        "field": "serper_api_key",
        "label": "Serper Key",
        "category": "联网搜索验证",
        "required": "选填",
        "required_level": "optional",
        "description": "Google搜索聚合,用于域名限定检索(如SEC/新闻/学术站)。不填则走 Tavily 单通道,垂直领域召回率下降。",
        "placeholder": "…",
        "secret": True,
    },
    {
        "key": "GOOGLE_FACTCHECK_API_KEY",
        "field": "google_factcheck_api_key",
        "label": "Google FactCheck Key",
        "category": "联网搜索验证",
        "required": "选填",
        "required_level": "optional",
        "description": "Google事实核查库补充,命中时可信度高但覆盖小。不填不影响主流程。",
        "placeholder": "AIza…",
        "secret": True,
    },
    # —— 模型微调:改名/换模型,保存即全局同步 ——
    {
        "key": "NLI_GROQ_MODEL",
        "field": "nli_groq_model",
        "label": "NLI 判定模型",
        "category": "模型微调",
        "required": "选填",
        "required_level": "optional",
        "description": "NLI 判定(Groq)用的模型名,默认 openai/gpt-oss-20b。保存后判定链路即时生效,无需重启。",
        "placeholder": "openai/gpt-oss-20b",
        "secret": False,
    },
    {
        "key": "CLAIM_EXTRACTION_MODEL",
        "field": "claim_extraction_model",
        "label": "声明提取模型",
        "category": "模型微调",
        "required": "选填",
        "required_level": "optional",
        "description": "声明提取覆盖当前通道的模型名,留空用各通道默认(OpenAI 兼容名都可用)。保存后即时生效。",
        "placeholder": "留空=通道默认",
        "secret": False,
    },
]


def _mask(value: str | None) -> str:
    if not value:
        return ""
    v = value.strip().strip('"').strip("'")
    if len(v) <= 8:
        return "****"
    return f"****{v[-4:]}"


def _read_env_file() -> list[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def _write_env_values(values: dict[str, str]) -> None:
    """更新 backend/.env:存在则替换,不存在则追加;同时同步 os.environ。"""
    lines = _read_env_file()
    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        replaced = False
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in values:
                new_lines.append(f"{k}={values[k]}")
                updated_keys.add(k)
                replaced = True
        if not replaced:
            new_lines.append(line)
    for k, v in values.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    for k, v in values.items():
        os.environ[k] = v


def _reload_runtime() -> None:
    """清掉 Settings 缓存与全部 LLM/Key 单例,下一次调用自动用新Key生效,无需重启。

    凡是 __init__ 里读过 Key/模型名的单例都必须在这里重置,否则改设置后
    还拿旧值(之前漏过 NLI/路由/verifier，表现为改 Key 不生效只能重启)。
    """
    get_settings.cache_clear()

    def _reset(modname: str, varname: str):
        try:
            mod = __import__(modname, fromlist=[varname])
            setattr(mod, varname, None)
        except Exception:
            pass

    _reset("app.utils.llm_clients", "_llm_client")
    _reset("app.core.claim_extractor", "_extractor")
    _reset("app.core.claim_adjudicator", "_adjudicator")
    _reset("app.core.nli_model", "_nli_model")
    _reset("app.core.domain_source_router", "_router")
    _reset("app.core.verifier", "_verifier")
    _reset("app.core.web_search", "_searcher")


def _effective_summary() -> dict:
    s = get_settings()
    models = [mid for mid, info in s.supported_models.items() if not info.get("api_key_field") or bool(getattr(s, info["api_key_field"], None))]
    claim_ready = bool(s.groq_api_key or s.nvidia_api_key or s.openrouter_api_key)
    adjudicator = "gemini" if s.gemini_api_key else ("zen/openai-compatible" if s.zen_api_key else "fallback(启发式)")
    return {
        "claim_ready": claim_ready,
        "claim_channel": "groq > nvidia > openrouter" if claim_ready else "未配置(仅启发式兜底)",
        "claim_model": (s.claim_extraction_model or "").strip() or "通道默认",
        "nli_model": (s.nli_groq_model or "").strip() or "openai/gpt-oss-20b",
        "adjudicator": adjudicator,
        "chat_models": models,
        "chat_models_count": len(models),
        "web_search": bool(s.tavily_api_key or s.serper_api_key),
        "web_providers": [p for p, on in [("tavily", bool(s.tavily_api_key)), ("serper", bool(s.serper_api_key)), ("factcheck", bool(s.google_factcheck_api_key))] if on],
    }


class SettingsUpdate(BaseModel):
    values: dict[str, str] = Field(default_factory=dict, description="ENV_KEY -> 新值;空字符串表示清空该Key;未出现的Key保持不变")


@router.get("/settings/schema")
async def settings_schema():
    """返回设置项元数据,前端按 category 分组渲染。"""
    cats: list[str] = []
    for d in SETTINGS_DEFS:
        if d["category"] not in cats:
            cats.append(d["category"])
    return {"categories": cats, "items": SETTINGS_DEFS}


@router.get("/settings/status")
async def settings_status():
    """返回每项是否已配置(脱敏)+当前生效总览。绝不返回明文Key。"""
    s = get_settings()
    items = []
    for d in SETTINGS_DEFS:
        raw = getattr(s, d["field"], None)
        val = str(raw) if raw is not None else ""
        configured = bool(val and val.strip())
        items.append(
            {
                "key": d["key"],
                "label": d["label"],
                "category": d["category"],
                "required": d["required"],
                "required_level": d["required_level"],
                "description": d["description"],
                "secret": d["secret"],
                "placeholder": d["placeholder"],
                "configured": configured,
                "masked": _mask(val) if d["secret"] else val,
                "value": "" if d["secret"] else val,  # URL/模型名明文回显,方便编辑
            }
        )
    return {"items": items, "effective": _effective_summary()}


@router.put("/settings")
async def update_settings(body: SettingsUpdate):
    """保存Key/URL并热重载。前端只传用户改过的项即可。"""
    allowed = {d["key"] for d in SETTINGS_DEFS}
    to_write: dict[str, str] = {}
    for k, v in (body.values or {}).items():
        if k not in allowed:
            continue
        to_write[k] = (v or "").strip().strip('"').strip("'")
    if not to_write:
        return {"ok": True, "updated": [], "effective": _effective_summary()}
    _write_env_values(to_write)
    _reload_runtime()
    logger.info(f"Settings updated via web: {list(to_write.keys())}")
    return {"ok": True, "updated": list(to_write.keys()), "effective": _effective_summary()}


@router.post("/settings/effective")
async def settings_effective():
    """查询当前保存值是否已生效(保存后前端调用一次即可)。"""
    return {"ok": True, "effective": _effective_summary()}
# ── 连通测试 ──────────────────────────────────────────────────────────────

class SettingsTestRequest(BaseModel):
    key: str = Field(..., description="ENV_KEY,如 GROQ_API_KEY")
    value: Optional[str] = Field(
        default=None,
        description="待测明文(仅本次测试,不保存);不传则测已保存值",
    )


def _sanitize(detail: str, secret: str | None) -> str:
    """报错信息脱敏:避免把待测Key原文带回前端。"""
    if secret and secret in detail:
        detail = detail.replace(secret, "***")
    return detail[:300]


async def _test_openai_models(base_url: str, api_key: str, extra_headers: dict | None = None) -> tuple[bool, str]:
    from openai import AsyncOpenAI

    kwargs: dict = {"base_url": base_url, "api_key": api_key, "timeout": 15.0}
    if extra_headers:
        kwargs["default_headers"] = extra_headers
    client = AsyncOpenAI(**kwargs)
    models = await client.models.list()
    n = len(models.data) if getattr(models, "data", None) else 0
    return True, f"连接成功,远端模型列表 {n} 个"


@router.post("/settings/test")
async def test_settings_key(body: SettingsTestRequest):
    """测试单个Key/URL是否连通。

    传 value 则测该草稿值(不保存,适合保存前验证);
    不传则测当前已保存值。搜索类测试为最小请求,
    Tavily/Serper 会消耗 1 次查询额度,页面上有提示。
    """
    allowed = {d["key"] for d in SETTINGS_DEFS}
    if body.key not in allowed:
        return {"ok": False, "detail": f"未知配置项: {body.key}", "latency_ms": 0, "tested_with": "none"}

    s = get_settings()
    field = next(d["field"] for d in SETTINGS_DEFS if d["key"] == body.key)
    candidate = (body.value or "").strip().strip('"').strip("'")
    tested_with = "draft"
    if not candidate:
        saved = getattr(s, field, None)
        candidate = str(saved).strip() if saved else ""
        tested_with = "saved"
    if not candidate and body.key not in ("ZEN_BASE_URL", "ZEN_MODEL"):
        return {"ok": False, "detail": "Key 为空,请先输入后再测试", "latency_ms": 0, "tested_with": "none"}

    t0 = time.perf_counter()
    try:
        if body.key == "GROQ_API_KEY":
            ok, detail = await _test_openai_models("https://api.groq.com/openai/v1", candidate)
        elif body.key == "NVIDIA_API_KEY":
            ok, detail = await _test_openai_models("https://integrate.api.nvidia.com/v1", candidate)
        elif body.key == "OPENROUTER_API_KEY":
            ok, detail = await _test_openai_models(
                "https://openrouter.ai/api/v1",
                candidate,
                {"HTTP-Referer": "http://localhost:3000", "X-Title": "AI Hallucination Detector"},
            )
        elif body.key == "ZEN_API_KEY":
            base = str(s.zen_base_url or "").strip() or "https://opencode.ai/zen/go/v1"
            ok, detail = await _test_openai_models(base, candidate)
        elif body.key == "GEMINI_API_KEY":
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": candidate, "pageSize": 1},
                )
            if r.status_code == 200:
                ok, detail = True, "连接成功,Gemini Key 有效"
            else:
                try:
                    msg = r.json().get("error", {}).get("message", r.text)
                except Exception:
                    msg = r.text
                ok, detail = False, f"Gemini 返回 {r.status_code}: {msg}"
        elif body.key == "TAVILY_API_KEY":
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(
                    "https://api.tavily.com/search",
                    json={"api_key": candidate, "query": "ping", "max_results": 1,
                          "include_answer": False, "search_depth": "basic"},
                )
            if r.status_code == 200:
                ok, detail = True, "连接成功,已消耗 1 次查询额度"
            elif r.status_code in (401, 403):
                ok, detail = False, "Key 无效(401/403),请检查后重试"
            else:
                ok, detail = False, f"Tavily 返回 {r.status_code}: {r.text[:150]}"
        elif body.key == "SERPER_API_KEY":
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(
                    "https://google.serper.dev/search",
                    json={"q": "ping", "num": 1},
                    headers={"X-API-KEY": candidate, "Content-Type": "application/json"},
                )
            if r.status_code == 200:
                ok, detail = True, "连接成功,已消耗 1 次查询额度"
            elif r.status_code in (401, 403):
                ok, detail = False, "Key 无效(401/403),请检查后重试"
            else:
                ok, detail = False, f"Serper 返回 {r.status_code}: {r.text[:150]}"
        elif body.key == "GOOGLE_FACTCHECK_API_KEY":
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(
                    "https://factchecktools.googleapis.com/v1alpha1/claims:search",
                    params={"query": "test", "pageSize": 1, "key": candidate},
                )
            if r.status_code == 200:
                ok, detail = True, "连接成功,FactCheck Key 有效"
            elif r.status_code in (400, 401, 403):
                ok, detail = False, f"Key 无效({r.status_code}),请检查后重试"
            else:
                ok, detail = False, f"FactCheck 返回 {r.status_code}: {r.text[:150]}"
        elif body.key == "ZEN_BASE_URL":
            import httpx

            base = candidate.rstrip("/")
            if not base.startswith(("http://", "https://")):
                return {"ok": False, "detail": "URL 须以 http:// 或 https:// 开头", "latency_ms": 0, "tested_with": tested_with}
            headers = {"Authorization": f"Bearer {s.zen_api_key}"} if s.zen_api_key else {}
            try:
                async with httpx.AsyncClient(timeout=10.0) as c:
                    r = await c.get(f"{base}/models", headers=headers)
                if r.status_code == 200:
                    ok, detail = True, "网关地址可达(/models 正常)"
                elif r.status_code in (401, 403):
                    ok, detail = True, "网关地址可达(需有效 MiMo Key 鉴权)"
                else:
                    ok, detail = False, f"网关返回 {r.status_code},请检查地址"
            except Exception as e:
                ok, detail = False, f"网关不可达: {e}"
        elif body.key in ("ZEN_MODEL", "NLI_GROQ_MODEL", "CLAIM_EXTRACTION_MODEL"):
            if candidate:
                ok, detail = True, "模型名已填写,保存后相关链路将使用它"
            elif body.key == "CLAIM_EXTRACTION_MODEL":
                ok, detail = True, "已清空,保存后将使用通道默认模型"
            else:
                ok, detail = False, "模型名为空"
        else:
            ok, detail = False, "暂不支持该项测试"
    except Exception as e:
        ok, detail = False, f"连接失败: {e}"

    latency_ms = int((time.perf_counter() - t0) * 1000)
    return {"ok": ok, "detail": _sanitize(str(detail), candidate), "latency_ms": latency_ms, "tested_with": tested_with}

