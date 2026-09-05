"""
LLM API clients for multi-model chat support.
- Groq
- NVIDIA NIM
- OpenRouter
"""

import logging
from typing import AsyncGenerator, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


def _effective_model(provider: str, model_id: str) -> str:
    """Zen 通道实际发给网关的模型名：永远跟随设置里的 ZEN_MODEL。

    下拉框传来的 id 固定是 "mimo-v2.5"(历史会话兼容)，真正出站时替换，
    这样设置页改名后聊天/外部API调用立刻同步，无需重启。
    """
    if provider == "zen":
        custom = (get_settings().zen_model or "").strip()
        if custom:
            return custom
    return model_id


class LLMClient:
    def __init__(self):
        self.settings = get_settings()
        self._clients: dict[str, object] = {}

    def _get_openai_compatible_client(self, provider: str):
        """
        Get an OpenAI-compatible async client for a given provider.
        Groq, NVIDIA NIM, and OpenRouter all support the OpenAI API format.
        """
        if provider in self._clients:
            return self._clients[provider]

        from openai import AsyncOpenAI

        provider_config = {
            "groq": {
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": self.settings.groq_api_key,
            },
            "nvidia": {
                "base_url": "https://integrate.api.nvidia.com/v1",
                "api_key": self.settings.nvidia_api_key,
            },
            "openrouter": {
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": self.settings.openrouter_api_key,
            },
            "zen": {
                "base_url": self.settings.zen_base_url,
                "api_key": self.settings.zen_api_key,
            },
        }

        config = provider_config.get(provider)
        if not config or not config["api_key"]:
            return None

        client = AsyncOpenAI(
            base_url=config["base_url"],
            api_key=config["api_key"],
        )
        self._clients[provider] = client
        return client


    async def chat(
        self,
        model_id: str,
        message: str,
        conversation_history: Optional[list[dict]] = None,
    ) -> str:
        """
        Send a message to the specified LLM and return the response.
        
        Args:
            model_id: Model identifier from supported_models registry
            message: User's message
            conversation_history: Previous messages [{role, content}, ...]
            
        Returns:
            The model's response text.
        """
        model_info = self.settings.supported_models.get(model_id)
        if not model_info:
            raise ValueError(f"Unsupported model: {model_id}")

        provider = model_info["provider"]
        history = conversation_history or []

        if provider in ("groq", "nvidia", "openrouter", "zen"):
            return await self._chat_openai_compatible(provider, model_id, message, history)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    async def chat_stream(
        self,
        model_id: str,
        message: str,
        conversation_history: Optional[list[dict]] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Stream a response from the specified LLM.
        Yields response text chunks.
        """
        model_info = self.settings.supported_models.get(model_id)
        if not model_info:
            raise ValueError(f"Unsupported model: {model_id}")

        provider = model_info["provider"]
        history = conversation_history or []

        if provider in ("groq", "nvidia", "openrouter", "zen"):
            async for chunk in self._stream_openai_compatible(provider, model_id, message, history):
                yield chunk

    # ── OpenAI-Compatible (Groq, NVIDIA NIM, OpenRouter) ──────────────

    async def _chat_openai_compatible(
        self, provider: str, model_id: str, message: str, history: list[dict]
    ) -> str:
        client = self._get_openai_compatible_client(provider)
        if not client:
            raise RuntimeError(f"{provider} API key not configured")

        messages = [{"role": m["role"], "content": m["content"]} for m in history]
        messages.append({"role": "user", "content": message})

        extra_kwargs = {}
        if provider == "openrouter":
            extra_kwargs["extra_headers"] = {
                "HTTP-Referer": "http://localhost:3000",
                "X-Title": "AI Hallucination Detector",
            }

        response = await client.chat.completions.create(
            model=_effective_model(provider, model_id),
            messages=messages,
            **extra_kwargs,
        )
        try:
            from app.core.provider_usage import record_provider_call
            await record_provider_call(provider)
        except Exception:
            pass
        return response.choices[0].message.content

    async def _stream_openai_compatible(
        self, provider: str, model_id: str, message: str, history: list[dict]
    ):
        client = self._get_openai_compatible_client(provider)
        if not client:
            raise RuntimeError(f"{provider} API key not configured")

        messages = [{"role": m["role"], "content": m["content"]} for m in history]
        messages.append({"role": "user", "content": message})

        extra_kwargs = {}
        if provider == "openrouter":
            extra_kwargs["extra_headers"] = {
                "HTTP-Referer": "http://localhost:3000",
                "X-Title": "AI Hallucination Detector",
            }

        stream = await client.chat.completions.create(
            model=_effective_model(provider, model_id),
            messages=messages,
            stream=True,
            **extra_kwargs,
        )
        try:
            from app.core.provider_usage import record_provider_call
            await record_provider_call(provider)
        except Exception:
            pass
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


# ── Module-level singleton ────────────────────────────────────────────────

_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Get or create the LLM client singleton."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
