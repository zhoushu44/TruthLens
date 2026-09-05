"""
LLM-powered claim extraction from AI responses.
"""

import json
import logging
from typing import Optional

from openai import AsyncOpenAI

from app.config import get_settings
from app.models.detect import (
    ExtractedClaim,
    ClaimDomain,
    SourceType,
    ConversationMessage,
)

logger = logging.getLogger(__name__)

# ── Claim Extraction Prompt ───────────────────────────────────────────────

CLAIM_EXTRACTION_PROMPT = """You are a precise claim extraction system. Your job is to analyze an AI-generated response and extract every individual factual claim that can be independently verified.

## LANGUAGE (HIGHEST PRIORITY — overrides everything below)
- Detect the language of the AI's response FIRST.
- The "text" field MUST be written in that SAME language. Chinese response → "text" in Simplified Chinese. English response → "text" in English. NEVER translate a Chinese response into English claims.
- "exact_quote" is always the verbatim substring (same language as response, by definition).
- JSON keys, "domain", "suggested_sources" stay in English.
- "search_queries": use English for scientific/technical topics, otherwise the response language.

## Instructions

1. Extract each factual claim as a **standalone assertion** that can be verified independently (stored in "text").
   **LANGUAGE RULE**: Write "text" in the SAME language as the AI's response — see LANGUAGE section at the top, it has the highest priority. JSON keys, "domain", and "suggested_sources" stay in English. "search_queries" should be in English whenever the claim involves scientific/technical topics (for better retrieval), otherwise match the response language.
2. **CRITICAL**: For every claim, you MUST extract the exact, strictly matching verbal substring from the AI's response that corresponds to this claim (stored in "exact_quote"). This will be used for exact text highlighting in the UI.
3. Classify each claim by **domain** (see domain list below).
4. For opinion claims: extract them if they are stated as objective fact (e.g., "X is the best") — classify as "opinion_subjective". Skip clearly hedged opinions ("I think", "it's possible").
5. DO extract: facts, statistics, dates, names, definitions, causal claims, comparisons, scientific assertions, financial data.
6. Rate the importance of each claim (0-1): how critical is this claim to the overall response?
7. Rate the confidence that this claim needs checking (0-1): how likely is it to be hallucinated?
8. Suggest which verification sources to check: web_search, conversation_history, vector_db, direct_api.
9. Suggest specific search queries for web verification.
10. Extract any numerical citation indices (e.g., [1], [2]) that the AI embedded in the text related to this claim into `citation_indices` (as a list of integers).
11. List key entities (names, places, organizations, numbers) in each claim.
12. Set `requires_multi_hop` to true if the claim requires combining information from multiple sources to verify.

## Domain Classification

Classify each claim into ONE of these domains:
- **general_factual**: Common knowledge, geography, culture (e.g., "The Eiffel Tower is 330m tall")
- **scientific_technical**: Physics, CS, engineering, biology, chemistry (e.g., "Transformers use self-attention")
- **medical_health**: Diseases, treatments, drugs, anatomy, nutrition (e.g., "Aspirin reduces heart attack risk by 25%")
- **numerical_statistical**: Statistics, percentages, measurements, rankings (e.g., "GDP grew 3.2% in Q3 2025")
- **finance_business**: Companies, stocks, revenue, regulations, crypto (e.g., "Apple's revenue was $394B in FY2023")
- **legal_regulatory**: Laws, court rulings, regulations, compliance (e.g., "GDPR requires consent for data processing")
- **news_current_events**: Recent happenings, politics, world events (e.g., "The EU passed the AI Act in March 2024")
- **historical**: Past events, dates, historical figures (e.g., "The Berlin Wall fell on Nov 9, 1989")
- **causal_relational**: Cause-effect, correlations, comparisons (e.g., "Smoking causes lung cancer")
- **opinion_subjective**: Personal views stated as fact (e.g., "Python is the best language for ML")

## Output Format

Return ONLY valid JSON with this exact structure:
{
  "claims": [
    {
      "id": "c1",
      "text": "The exact factual claim as a standalone assertion (SAME language as response)",
      "text_en": "English translation of the claim (if text is already English, repeat it verbatim)",
      "exact_quote": "The exact verbatim phrase from the original response",
      "citation_indices": [1, 2],
      "domain": "medical_health",
      "importance": 0.8,
      "suggested_sources": ["web_search", "direct_api"],
      "search_queries": ["search query for this claim"],
      "confidence_needs_checking": 0.7,
      "key_entities": ["Entity1", "Entity2"],
      "requires_multi_hop": false
    }
  ]
}

If the response contains no verifiable factual claims, return: {"claims": []}
"""


# ── Provider configurations for claim extraction ─────────────────────────

EXTRACTION_PROVIDERS = [
    {
        "name": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_field": "groq_api_key",
        "model": "openai/gpt-oss-20b",
        "description": "Groq gpt-oss 20B — fast free option, good JSON output",
    },
    {
        "name": "nvidia",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_field": "nvidia_api_key",
        "model": "meta/llama-3.1-70b-instruct",
        "description": "NVIDIA NIM Llama 3.1 70B ",
    },
    {
        "name": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_field": "openrouter_api_key",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "description": "OpenRouter Llama 3.3 70B — free tier",
    },
]


class ClaimExtractor:
    """
    Extracts verifiable claims from AI responses using the best available
    free LLM provider.
    """

    def __init__(self):
        settings = get_settings()
        self.max_claims = settings.max_claims_per_response
        
        # Find the best available provider (all OpenAI-compatible)
        self.client = None
        self.model_name = None
        self.provider_name = None

        for provider in EXTRACTION_PROVIDERS:
            api_key = getattr(settings, provider["api_key_field"], None)
            if api_key:
                self.client = AsyncOpenAI(
                    base_url=provider["base_url"],
                    api_key=api_key,
                )
                self.model_name = provider["model"]
                self.provider_name = provider["name"]
                # 设置页可覆盖模型名(留空=各通道默认)，改后热重载即生效
                override = (getattr(settings, "claim_extraction_model", "") or "").strip()
                if override:
                    self.model_name = override
                logger.info(f"Claim extraction: using {provider['description']}")
                break

        # If no OpenAI-compatible provider available
        if not self.client:
            logger.warning("No API keys configured for claim extraction! Please set GROQ_API_KEY, NVIDIA_API_KEY, or OPENROUTER_API_KEY in .env")

    @property
    def is_available(self) -> bool:
        return self.client is not None

    async def extract_claims(
        self,
        ai_response: str,
        conversation_history: Optional[list[ConversationMessage]] = None,
        has_documents: bool = False,
    ) -> list[ExtractedClaim]:
        """
        Extract verifiable claims from an AI response.

        Args:
            ai_response: The AI-generated response to analyze.
            conversation_history: Previous conversation messages for context.
            has_documents: Whether user-uploaded documents are available.

        Returns:
            List of ExtractedClaim objects.
        """
        if not self.is_available:
            logger.error("Cannot extract claims: no provider configured")
            return []

        # Build the user message with context
        user_message = self._build_extraction_message(
            ai_response, conversation_history, has_documents
        )

        try:
            if self.client:
                response = await self._call_openai_compatible(user_message)
            else:
                raise RuntimeError("No claim extraction provider client available")

            claims = self._parse_response(response)
            claims = claims[: self.max_claims]

            logger.info(f"Extracted {len(claims)} claims via {self.provider_name}")
            return claims

        except Exception as e:
            logger.error(f"Claim extraction failed: {e}", exc_info=True)
            return []

    def _build_extraction_message(
        self,
        ai_response: str,
        conversation_history: Optional[list[ConversationMessage]],
        has_documents: bool,
    ) -> str:
        """Build the message to send for claim extraction."""
        parts = []

        if conversation_history:
            conv_text = "\n".join(
                f"{msg.role.upper()}: {msg.content}"
                for msg in conversation_history[-6:]
            )
            parts.append(f"## Conversation Context\n{conv_text}")

        source_note = "Available verification sources: web_search, conversation_history"
        if has_documents:
            source_note += ", vector_db (user has uploaded documents)"
        parts.append(source_note)

        parts.append(f"## AI Response to Analyze\n{ai_response}")

        return "\n\n".join(parts)

    async def _call_openai_compatible(self, user_message: str) -> str:
        """
        Call claim extraction via OpenAI-compatible API.
        Works with Groq, NVIDIA NIM, and OpenRouter.
        """
        extra_kwargs = {}
        
        # OpenRouter needs extra headers
        if self.provider_name == "openrouter":
            extra_kwargs["extra_headers"] = {
                "HTTP-Referer": "http://localhost:3000",
                "X-Title": "AI Hallucination Detector",
            }

        response = await self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": CLAIM_EXTRACTION_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
            **extra_kwargs,
        )
        # 记一次上游调用（成功才记，失败不记；内部已容错）
        try:
            from app.core.provider_usage import record_provider_call
            await record_provider_call(self.provider_name or "unknown")
        except Exception:
            pass
        return response.choices[0].message.content


    def _parse_response(self, response_text: str) -> list[ExtractedClaim]:
        """Parse the JSON response into ExtractedClaim objects."""
        import re
        try:
            text = response_text.strip()
            
            # Isolate the JSON object from potential markdown wrapping or conversational prefixes
            match = re.search(r'(\{.*\})', text, re.DOTALL)
            if match:
                text = match.group(1)
                
            # Automatically strip trailing commas (common LLM hallucination) before closing brackets
            text = re.sub(r',\s*([}\]])', r'\1', text)
            
            data = json.loads(text)
            claims_data = data.get("claims", [])

            claims = []
            for i, item in enumerate(claims_data):
                try:
                    claim = ExtractedClaim(
                        id=item.get("id", f"c{i + 1}"),
                        text=item.get("text", ""),
                        text_en=item.get("text_en"),
                        exact_quote=item.get("exact_quote"),
                        citation_indices=item.get("citation_indices", []),
                        domain=self._parse_claim_domain(item.get("domain", "general_factual")),
                        importance=float(item.get("importance", 0.5)),
                        suggested_sources=self._parse_sources(item.get("suggested_sources", [])),
                        search_queries=item.get("search_queries", []),
                        confidence_needs_checking=float(
                            item.get("confidence_needs_checking", 0.5)
                        ),
                        key_entities=item.get("key_entities", []),
                        requires_multi_hop=bool(item.get("requires_multi_hop", False)),
                    )
                    if claim.text:
                        claims.append(claim)
                except Exception as e:
                    logger.warning(f"Failed to parse claim {i}: {e}")
                    continue

            return claims

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse response as JSON: {e}")
            logger.debug(f"Raw response: {response_text[:500]}")
            return []

    @staticmethod
    def _parse_claim_domain(domain_str: str) -> ClaimDomain:
        """Parse domain string to ClaimDomain enum, with fallback mapping from old types."""
        # Direct match
        try:
            return ClaimDomain(domain_str.lower())
        except ValueError:
            pass
        # Map old ClaimType values to new ClaimDomain
        old_type_map = {
            "factual": ClaimDomain.GENERAL_FACTUAL,
            "statistical": ClaimDomain.NUMERICAL_STATISTICAL,
            "temporal": ClaimDomain.HISTORICAL,
            "causal": ClaimDomain.CAUSAL_RELATIONAL,
            "definition": ClaimDomain.GENERAL_FACTUAL,
        }
        return old_type_map.get(domain_str.lower(), ClaimDomain.GENERAL_FACTUAL)

    @staticmethod
    def _parse_sources(sources: list) -> list[SourceType]:
        parsed = []
        for s in sources:
            try:
                parsed.append(SourceType(s))
            except ValueError:
                continue
        return parsed


# ── Module-level singleton ────────────────────────────────────────────────

_extractor: Optional[ClaimExtractor] = None


def get_claim_extractor() -> ClaimExtractor:
    """Get or create the claim extractor singleton."""
    global _extractor
    if _extractor is None:
        _extractor = ClaimExtractor()
    return _extractor
