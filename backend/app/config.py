"""
Application configuration loaded from environment variables.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://detection_admin:detection_pass@localhost:5433/ai_detection",
        description="Async PostgreSQL connection string",
    )
    database_url_sync: str = Field(
        default="postgresql://detection_admin:detection_pass@localhost:5433/ai_detection",
        description="Sync PostgreSQL connection string (for Alembic)",
    )


    # ── Embeddings (NVIDIA NIM) ────────────────────────────────
    embedding_model: str = Field(
        default="NV-Embed-QA",
        description="NVIDIA NIM embedding model name",
    )
    embedding_dimensions: int = Field(
        default=1024,
        description="Embedding vector dimensions for NV-Embed-QA",
    )


    # OpenRouter 
    openrouter_api_key: Optional[str] = Field(
        default=None,
        description="OpenRouter API key",
    )

    # NVIDIA NIM 
    nvidia_api_key: Optional[str] = Field(
        default=None,
        description="NVIDIA NIM API key",
    )

    # Groq 
    groq_api_key: Optional[str] = Field(
        default=None,
        description="Groq API key",
    )

    # ── Web Search ────────────────────────────────────────────────────────
    # Tavily 
    tavily_api_key: Optional[str] = Field(
        default=None,
        description="Tavily API key",
    )
    # Serper (domain-filtered Google search)
    serper_api_key: Optional[str] = Field(
        default=None,
        description="Serper.dev API key for domain-filtered web search",
    )

    # ── Gemini (Claim Adjudication via Google AI Studio) ──────────────────
    gemini_api_key: Optional[str] = Field(
        default=None,
        description="Google Gemini API key from AI Studio (for claim adjudication)",
    )

    # ── OpenAI-compatible adjudicator (自定义仲裁模型槽位：Zen / Groq直连 / Ollama / vLLM 等) ─────────
    zen_api_key: Optional[str] = Field(
        default=None,
        description="API key for the OpenAI-compatible adjudicator (key of whichever base_url you point to)",
    )
    zen_base_url: str = Field(
        default="https://opencode.ai/zen/go/v1",
        description="Base URL for OpenAI-compatible adjudication gateway",
    )
    zen_model: str = Field(
        default="mimo-v2.5",
        description="Model id for OpenAI-compatible claim adjudication",
    )
    adjudication_mode: str = Field(
        default="joint",
        description="Claim adjudication mode: 'joint' (multiple claims in one call) or 'perclaim' (one LLM call per claim)",
    )

    # ── Google Fact Check Tools API ───────────────────────────────────────
    google_factcheck_api_key: Optional[str] = Field(
        default=None,
        description="Google Fact Check Tools API key from GCP Console",
    )

    # ── NLI Judge (Groq API) ──────────────────────────────────────────────
    nli_groq_model: str = Field(
        default="openai/gpt-oss-20b",
        description="Fast Groq model used as NLI judge (batch entailment scoring)",
    )
    # 兼容字段：旧版本地 NLI 用过的 NLI_DEVICE 环境变量可能仍被外部注入，
    # 保留此字段可避免 Settings extra=forbid 导致启动失败；现 NLI 走 Groq API，不再读取它。
    nli_device: str = Field(default="auto")

    # ── Claim Extraction ──────────────────────────────────────────────────
    claim_extraction_model: str = Field(
        default="",
        description="Override model for claim extraction (empty = per-provider default)",
    )

    # ── Pipeline Config ───────────────────────────────────────────────────
    claim_confidence_threshold: float = Field(
        default=0.3,
        description="Minimum confidence for a claim to be verified",
    )
    web_search_enabled: bool = Field(
        default=True,
        description="Enable web search as a verification source",
    )
    max_claims_per_response: int = Field(
        default=20,
        description="Maximum number of claims to extract per response",
    )

    # ── Evidence Pipeline Settings ────────────────────────────────────────
    max_evidence_per_claim: int = Field(
        default=10,
        description="Max evidence pieces to send to the LLM adjudicator per claim",
    )
    min_evidence_informativeness: float = Field(
        default=0.3,
        description="Min max(entailment, contradiction) NLI score to include evidence",
    )

    # ── Server ────────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=6655)
    debug: bool = Field(default=True)
    truthlens_api_key: Optional[str] = Field(default=None)
    api_key_required: bool = Field(default=False)

    # ── Supported LLM Models ──────────────────────────────────
    @property
    def supported_models(self) -> dict:
        """
        Registry of all supported LLM models
        Uses 4 providers:
        - Groq
        - NVIDIA NIM  
        - Gemini (Google AI Studio)
        - OpenRouter
        """
        models = {

            # ── Groq (ids verified live, 2026-09) ───────
            "openai/gpt-oss-120b": {
                "name": "gpt-oss 120B (Groq)",
                "provider": "groq",
                "tier": 1,
                "api_key_field": "groq_api_key",
                "description": "OpenAI open-weight flagship, strong reasoning on Groq",
            },
            "openai/gpt-oss-20b": {
                "name": "gpt-oss 20B (Groq)",
                "provider": "groq",
                "tier": 2,
                "api_key_field": "groq_api_key",
                "description": "Small fast open model on Groq",
            },

            # ── Zen gateway (OpenAI-compatible) ──
            "mimo-v2.5": {
                "name": "MiMo V2.5 (Zen)",
                "provider": "zen",
                "tier": 1,
                "api_key_field": "zen_api_key",
                "description": "Xiaomi reasoning model via OpenCode Zen",
            },

            # ── NVIDIA NIM ──────────────────
            "meta/llama-3.1-70b-instruct": {
                "name": "Llama 3.1 70B (NVIDIA)",
                "provider": "nvidia",
                "tier": 1,
                "api_key_field": "nvidia_api_key",
                "description": "Meta Llama on NVIDIA DGX Cloud",
            },
            "mistralai/mistral-7b-instruct-v0.3": {
                "name": "Mistral 7B (NVIDIA)",
                "provider": "nvidia",
                "tier": 2,
                "api_key_field": "nvidia_api_key",
                "description": "Mistral 7B on NVIDIA infrastructure",
            },
            "google/gemma-2-9b-it": {
                "name": "Gemma 2 9B (NVIDIA)",
                "provider": "nvidia",
                "tier": 2,
                "api_key_field": "nvidia_api_key",
                "description": "Google's open model Gemma 2 on NVIDIA NIM",
            },

            # ── Gemini (Google AI Studio) ─────
            "gemini-3-flash-preview": {
                "name": "Gemini 3 Flash Preview",
                "provider": "gemini",
                "tier": 1,
                "api_key_field": "gemini_api_key",
                "description": "Google's next generation lightweight model",
            },
            "gemma-3-27b-it": {
                "name": "Gemma 3 27B IT",
                "provider": "gemini",
                "tier": 2,
                "api_key_field": "gemini_api_key",
                "description": "Google's Gemma 3 open model on AI Studio",
            },
            "gemma-4-31b-it": {
                "name": "Gemma 4 31B IT",
                "provider": "gemini",
                "tier": 2,
                "api_key_field": "gemini_api_key",
                "description": "Google's latest Gemma 4 open model on AI Studio",
            },

            # ── OpenRouter ────────────────────
            "meta-llama/llama-3.3-70b-instruct:free": {
                "name": "Llama 3.3 70B (OpenRouter)",
                "provider": "openrouter",
                "tier": 1,
                "api_key_field": "openrouter_api_key",
                "description": "Meta Llama via OpenRouter free tier",
            },

        }

        # ZEN_MODEL 自定义名：id 保持 "mimo-v2.5"(历史会话不 break)，
        # 下拉显示名 + 实际发给网关的模型名都用自定义值
        custom_zen = (self.zen_model or "").strip()
        if custom_zen and custom_zen != "mimo-v2.5":
            models["mimo-v2.5"] = {
                **models["mimo-v2.5"],
                "name": f"{custom_zen} (Zen)",
            }

        return models

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


@lru_cache()
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
