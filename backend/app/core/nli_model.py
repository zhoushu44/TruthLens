"""
NLI (Natural Language Inference) judge for claim verification.

Batches (evidence, claim) pairs to a fast Groq LLM acting as NLI judge.
No local model, no torch dependency.

Each pair is scored as {"entailment": x, "contradiction": y, "neutral": z}.
"""

import asyncio
import json
import logging
import re
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

# Fallback when a batch fails for a pair (keeps pipeline running)
_NEUTRAL = {"entailment": 0.0, "contradiction": 0.0, "neutral": 1.0}

NLI_JUDGE_PROMPT = """You are a Natural Language Inference judge. For each numbered item, compare the EVIDENCE (premise) against the CLAIM (hypothesis) and assign probabilities that sum to 1.0.

- entailment: the evidence SUPPORTS / implies the claim
- contradiction: the evidence CONTRADICTS the claim
- neutral: the evidence is inconclusive about the claim (does not mention it, or lacks key details)

Judge semantics, not wording. Evidence and claim may be in Chinese or English — judge meaning either way.

Return ONLY valid JSON with this exact structure:
{{"results": [{{"entailment": 0.9, "contradiction": 0.05, "neutral": 0.05}}, ...]}}

Items:
{items}
"""


class NLIModel:
    """
    Groq-based NLI judge for claim verification.

    Classifies (evidence, claim) pairs into:
    - ENTAILMENT: evidence supports claim
    - CONTRADICTION: evidence contradicts claim
    - NEUTRAL: evidence is inconclusive about claim
    """

    # Pairs per LLM call (keeps JSON output reliable)
    NLI_GROQ_BATCH_SIZE = 12

    def __init__(self):
        settings = get_settings()
        self.groq_model = (settings.nli_groq_model or "").strip() or "openai/gpt-oss-20b"
        self._groq_api_key = settings.groq_api_key
        self._client = None
        self._loaded = False
        # 轻量去重缓存：同一 evidence×claim 在多 claim 间常重复，直接命中免推理
        self._cache: dict[tuple[str, str], dict[str, float]] = {}

    def describe(self) -> dict:
        """Backend info for /health and startup logs."""
        return {"backend": "groq", "model": self.groq_model, "device": "api"}

    def load(self):
        """Initialize the Groq client. Call during app startup."""
        if self._loaded:
            return
        if not self._groq_api_key:
            raise RuntimeError("GROQ_API_KEY not set — NLI judge unavailable")
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=self._groq_api_key,
        )
        self._loaded = True
        logger.info(f"NLI backend: Groq judge ({self.groq_model}), no local model loaded")

    async def predict(
        self, pairs: list[tuple[str, str]]
    ) -> list[dict[str, float]]:
        """
        Score (evidence_text, claim_text) pairs.

        Returns:
            List of score dicts with keys: entailment, contradiction, neutral.
        """
        if not pairs:
            return []

        # 缓存命中先摘除，只推理未见过的 pair（保持返回顺序）
        results: list[Optional[dict[str, float]]] = [None] * len(pairs)
        todo: list[tuple[str, str]] = []
        todo_idx: list[int] = []
        for i, p in enumerate(pairs):
            key = (p[0][:500], p[1][:500])
            hit = self._cache.get(key)
            if hit is not None:
                results[i] = hit
            else:
                todo.append(p)
                todo_idx.append(i)

        if todo:
            computed: list[dict[str, float]] = []
            for start in range(0, len(todo), self.NLI_GROQ_BATCH_SIZE):
                chunk = todo[start:start + self.NLI_GROQ_BATCH_SIZE]
                try:
                    scores = await self._judge_batch(chunk)
                except Exception as e:
                    logger.warning(f"Groq NLI batch failed ({len(chunk)} pairs): {e}")
                    scores = [dict(_NEUTRAL) for _ in chunk]
                computed.extend(scores)
            for i, res in zip(todo_idx, computed):
                results[i] = res
                key = (pairs[i][0][:500], pairs[i][1][:500])
                if len(self._cache) < 2000:
                    self._cache[key] = res

        return [r if r is not None else dict(_NEUTRAL) for r in results]

    async def predict_single(
        self, evidence: str, claim: str
    ) -> dict[str, float]:
        """
        Predict NLI scores for a single (evidence, claim) pair.

        Returns:
            Score dict: {"entailment": 0.9, "contradiction": 0.05, "neutral": 0.05}
        """
        results = await self.predict([(evidence, claim)])
        return results[0] if results else dict(_NEUTRAL)

    def get_label(self, scores: dict[str, float]) -> str:
        """Get the predicted NLI label from scores."""
        return max(scores, key=scores.get).upper()

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    async def _judge_batch(
        self, pairs: list[tuple[str, str]]
    ) -> list[dict[str, float]]:
        """One Groq call for up to NLI_GROQ_BATCH_SIZE pairs."""
        if self._client is None:
            raise RuntimeError("Groq NLI client not initialized (call load() first)")

        lines = []
        for i, (evidence, claim) in enumerate(pairs):
            lines.append(
                f'[{i}] EVIDENCE: "{evidence[:600]}"\n'
                f'[{i}] CLAIM: "{claim[:300]}"'
            )
        prompt = NLI_JUDGE_PROMPT.format(items="\n".join(lines))

        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                resp = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self.groq_model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.0,
                        max_tokens=2048,
                        response_format={"type": "json_object"},
                    ),
                    timeout=30.0,
                )
                return self._parse_judge_response(
                    resp.choices[0].message.content, len(pairs)
                )
            except Exception as e:
                last_error = e
                logger.warning(f"Groq NLI attempt {attempt + 1}/2 failed: {e}")
        raise last_error or RuntimeError("Groq NLI judge failed")

    def _parse_judge_response(
        self, response_text: str, expected: int
    ) -> list[dict[str, float]]:
        """Parse the judge's JSON into normalized score dicts."""
        text = (response_text or "").strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
        text = re.sub(r",\s*([}\]])", r"\1", text)
        data = json.loads(text)

        items = data.get("results", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError("Groq NLI response has no results list")

        parsed: list[dict[str, float]] = []
        for item in items[:expected]:
            try:
                ent = float(item.get("entailment", 0.0))
                con = float(item.get("contradiction", 0.0))
                neu = float(item.get("neutral", 0.0))
            except (TypeError, ValueError, AttributeError):
                ent, con, neu = 0.0, 0.0, 1.0
            total = ent + con + neu
            if total <= 0:
                ent, con, neu = 0.0, 0.0, 1.0
                total = 1.0
            parsed.append({
                "entailment": round(ent / total, 4),
                "contradiction": round(con / total, 4),
                "neutral": round(neu / total, 4),
            })

        # 数量对不上（模型漏项）时用 neutral 补齐，保证顺序和数量严格对应
        while len(parsed) < expected:
            parsed.append(dict(_NEUTRAL))
        return parsed


# ── Module-level singleton ────────────────────────────────────────────────

_nli_model: Optional[NLIModel] = None


def get_nli_model() -> NLIModel:
    """Get or create the NLI model singleton."""
    global _nli_model
    if _nli_model is None:
        _nli_model = NLIModel()
    return _nli_model
