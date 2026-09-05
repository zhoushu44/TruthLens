"""
AI Hallucination Detection System — FastAPI Application

Main entry point. Sets up the FastAPI app, registers routes,
configures CORS, and handles model loading on startup.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.api import analytics, detect, chat, documents, conversations, apikeys, openai_compat, settings

# ── Logging ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)-30s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── App Lifespan (Startup / Shutdown) ─────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events."""
    logger.info("=" * 60)
    logger.info("🛡️  AI Hallucination Detection System — Starting Up")
    logger.info("=" * 60)

    settings = get_settings()

    # Load NLI (Groq judge by default, local DeBERTa fallback)
    try:
        from app.core.nli_model import get_nli_model
        nli_model = get_nli_model()
        nli_model.load()
        logger.info(f"✅ NLI ready: {nli_model.describe()}")
    except Exception as e:
        logger.warning(f"⚠️  NLI Model failed to load: {e}")
        logger.warning("   Detection will work but NLI verification will be disabled")

    # Load NER model (spaCy)
    try:
        from app.core.ner_extractor import get_ner_extractor
        ner = get_ner_extractor()
        ner.load()
        if ner.is_loaded:
            logger.info("✅ NER Model loaded (spaCy)")
        else:
            logger.warning("⚠️  NER Model not available — install a spaCy model")
    except Exception as e:
        logger.warning(f"⚠️  NER Model failed to load: {e}")

    # Verify FREE API keys
    has_claim_provider = False
    if settings.groq_api_key:
        logger.info("✅ Groq key configured")
        has_claim_provider = True
    if settings.nvidia_api_key:
        logger.info("✅ NVIDIA NIM key configured")
        if not has_claim_provider:
            has_claim_provider = True
    if settings.openrouter_api_key:
        logger.info("✅ OpenRouter key configured")
        if not has_claim_provider:
            has_claim_provider = True

    if not has_claim_provider:
        logger.warning("⚠️  No LLM API key configured — claim extraction won't work!")

    # Web search
    if settings.tavily_api_key:
        logger.info("✅ Tavily web search configured")
    else:
        logger.warning("⚠️  Tavily API key not set — web verification disabled")

    # Count available providers
    available = sum([
        bool(settings.groq_api_key),
        bool(settings.nvidia_api_key),
        bool(settings.openrouter_api_key),
    ])
    logger.info(f"📊 {available}/3 LLM providers configured")
    try:
        from sqlalchemy import text as _sa_text
        from app.db.engine import engine as _engine
        async with _engine.begin() as _conn:
            await _conn.execute(_sa_text("""CREATE TABLE IF NOT EXISTS api_keys (
              id VARCHAR(36) PRIMARY KEY,
              name VARCHAR(200) NOT NULL DEFAULT 'default',
              prefix VARCHAR(20) NOT NULL,
              key_hash VARCHAR(128) NOT NULL UNIQUE,
              is_active BOOLEAN NOT NULL DEFAULT TRUE,
              expires_at TIMESTAMPTZ NULL,
              usage_count INTEGER NOT NULL DEFAULT 0,
              last_used_at TIMESTAMPTZ NULL,
              created_at TIMESTAMPTZ NULL DEFAULT NOW()
            )"""))
            await _conn.execute(_sa_text("CREATE INDEX IF NOT EXISTS ix_api_keys_prefix ON api_keys (prefix)"))
            await _conn.execute(_sa_text("""CREATE TABLE IF NOT EXISTS provider_usage (
              provider VARCHAR(50) PRIMARY KEY,
              calls INTEGER NOT NULL DEFAULT 0,
              last_used_at TIMESTAMPTZ NULL
            )"""))
            # 独立检测行归属模型用（无会话直调也落库统计）
            await _conn.execute(_sa_text("ALTER TABLE analysis_results ADD COLUMN IF NOT EXISTS model_id VARCHAR(100)"))
            # 全局知识库（无会话）上传：documents.conversation_id 必须允许 NULL
            # （老库由 001 迁移建成 NOT NULL；与 ORM nullable=True 不一致，启动时自愈）
            await _conn.execute(_sa_text("ALTER TABLE documents ALTER COLUMN conversation_id DROP NOT NULL"))
        logger.info("API Keys table ready")
    except Exception as _e:
        logger.warning(f"API Keys table init skipped: {_e}")
    try:
        if settings.truthlens_api_key:
            logger.info("Master API Key configured (TRUTHLENS_API_KEY)")
        else:
            logger.warning("TRUTHLENS_API_KEY not set — external calls run in compatible-open mode")
        if settings.api_key_required:
            logger.info("API_KEY_REQUIRED=True — detect/chat require valid Key")
    except Exception:
        pass

    logger.info("=" * 60)
    logger.info("🚀 Server ready!")
    logger.info("=" * 60)

    yield  # App is running

    # Shutdown
    logger.info("Shutting down...")


# ── FastAPI App ───────────────────────────────────────────────────────────

app = FastAPI(
    title="AI Hallucination Detection System",
    description=(
        "Detect, flag, and explain LLM hallucinations. "
        "Extracts claims from AI responses, verifies them against "
        "multiple sources (web, documents, conversation history), "
        "and provides detailed risk analysis."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────

app.include_router(detect.router, prefix="/api/v1", tags=["Detection"])
app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
app.include_router(documents.router, prefix="/api/v1", tags=["Documents"])
app.include_router(conversations.router, prefix="/api/v1", tags=["Conversations"])
app.include_router(analytics.router, prefix="/api/v1", tags=["Analytics"])
app.include_router(apikeys.router, prefix="/api/v1", tags=["API Keys"])
app.include_router(settings.router, prefix="/api/v1", tags=["Settings"])
app.include_router(openai_compat.router, prefix="/v1", tags=["OpenAI-Compatible"])


# ── Health Check ──────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check():
    """System health check."""
    from app.core.nli_model import get_nli_model
    from app.core.ner_extractor import get_ner_extractor

    settings = get_settings()
    nli = get_nli_model()
    ner = get_ner_extractor()

    return {
        "status": "healthy",
        "version": "0.1.0",
        "components": {
            "nli_model": {
                "loaded": nli.is_loaded,
                **nli.describe(),
            },
            "ner_model": {
                "loaded": ner.is_loaded,
            },
            "claim_extraction": {
                "available": any([
                    settings.groq_api_key,
                    settings.nvidia_api_key,
                    settings.openrouter_api_key,
                ]),
                "priority": "groq > nvidia > openrouter",
            },
            "web_search": {
                "available": bool(settings.tavily_api_key),
                "provider": "tavily",
                "enabled": settings.web_search_enabled,
            },
            "llm_providers": {
                "groq": bool(settings.groq_api_key),
                "nvidia": bool(settings.nvidia_api_key),
                "openrouter": bool(settings.openrouter_api_key),
                "zen": bool(settings.zen_api_key),
            },
        },
        "supported_models": list(settings.supported_models.keys()),
    }


@app.get("/api/v1/models", tags=["System"])
async def list_models():
    """List all available LLM models (only those with configured API keys)."""
    settings = get_settings()
    models = []
    for model_id, info in settings.supported_models.items():
        api_key_field = info.get("api_key_field")
        is_available = (
            api_key_field is None
            or bool(getattr(settings, api_key_field, None))
        )
        if not is_available:
            continue
        models.append({
            "id": model_id,
            "name": info["name"],
            "provider": info["provider"],
            "tier": info["tier"],
            "description": info.get("description", ""),
        })
    return {"models": models}



# ── 单端口部署：同 6655 端口同时提供 WebUI + API ─────────────
# Docker 构建时会把 frontend/dist 拷到 ../frontend_dist，存在就挂载
try:
    import os as _os
    from fastapi.responses import FileResponse as _FileResponse
    from fastapi.staticfiles import StaticFiles as _StaticFiles
    _here = _os.path.dirname(__file__)
    _candidates = [
        _os.path.join(_here, "..", "..", "frontend_dist"),
        _os.path.join(_here, "..", "frontend_dist"),
        "/app/frontend_dist",
        "/app/../frontend/dist",
        _os.path.join(_os.getcwd(), "frontend_dist"),
    ]
    _dist = next((p for p in _candidates if p and _os.path.isdir(p) and _os.path.isfile(_os.path.join(p, "index.html"))), None)
    if _dist:
        _assets = _os.path.join(_dist, "assets")
        if _os.path.isdir(_assets):
            app.mount("/assets", _StaticFiles(directory=_assets), name="web-assets")

        @app.get("/", include_in_schema=False)
        async def _serve_index():
            return _FileResponse(_os.path.join(_dist, "index.html"))

        @app.get("/{full_path:path}", include_in_schema=False)
        async def _serve_spa(full_path: str):
            if full_path.startswith(("api/", "v1/", "health", "docs", "openapi.json", "assets/")):
                from fastapi import HTTPException as _HTTP
                raise _HTTP(status_code=404)
            _fp = _os.path.join(_dist, full_path)
            if full_path and _os.path.isfile(_fp):
                return _FileResponse(_fp)
            return _FileResponse(_os.path.join(_dist, "index.html"))
except Exception:
    pass

