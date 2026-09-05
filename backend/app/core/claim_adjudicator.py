"""
Claim Adjudicator — Pipeline Stage 5.

Uses Gemini 3 Flash via the google-genai SDK to perform LLM-based
adjudication of each claim against its ranked evidence.

Replaces the old hardcoded risk scoring formula with intelligent
multi-hop reasoning over evidence pieces.
"""

import json
import asyncio
import logging
from typing import Optional
from dataclasses import dataclass

from app.config import get_settings
from app.models.detect import (
    ExtractedClaim, EvidencePiece, ClaimStatus, ClaimDomain,
)

logger = logging.getLogger(__name__)


@dataclass
class AdjudicationResult:
    """Result from LLM adjudication of a single claim."""
    claim: ExtractedClaim
    status: ClaimStatus
    risk_score: float
    confidence: float
    reasoning: str
    key_evidence_indices: list[int]
    contradiction_details: Optional[str]
    suggestion: Optional[str]


# ── Singleton ─────────────────────────────────────────────────────────────

_adjudicator: Optional["ClaimAdjudicator"] = None


def get_claim_adjudicator() -> "ClaimAdjudicator":
    """Get or create the claim adjudicator singleton."""
    global _adjudicator
    if _adjudicator is None:
        _adjudicator = ClaimAdjudicator()
    return _adjudicator


class ClaimAdjudicator:
    """
    LLM-based claim adjudicator using Gemini 3 Flash.

    For each claim + its ranked evidence (with NLI scores),
    produces a verdict with risk_score, reasoning, and confidence.
    """

    def __init__(self):
        settings = get_settings()
        self.backend = "none"
        # Prefer native Gemini; otherwise use any OpenAI-compatible
        # gateway (e.g. OpenCode Zen) when configured.
        if settings.gemini_api_key:
            try:
                from google import genai
                self.client = genai.Client(api_key=settings.gemini_api_key)
                self.model = "gemma-4-31b-it"
                self.backend = "gemini"
                logger.info("ClaimAdjudicator initialized with Gemini 3 Flash")
                return
            except Exception as e:
                logger.error(f"Failed to initialize Gemini client: {e}")

        if settings.zen_api_key:
            try:
                from openai import AsyncOpenAI
                self.client = AsyncOpenAI(
                    api_key=settings.zen_api_key,
                    base_url=settings.zen_base_url,
                )
                self.model = settings.zen_model
                self.backend = "openai"
                logger.info(f"ClaimAdjudicator initialized with OpenAI-compatible backend ({self.model})")
                return
            except Exception as e:
                logger.error(f"Failed to initialize OpenAI-compatible adjudicator client: {e}")

        logger.warning("No adjudicator LLM configured — will use fallback scoring")
        self.client = None

    def _build_prompt(self, claim: ExtractedClaim, evidence: list[EvidencePiece]) -> str:
        """Build the adjudication prompt for a single claim."""
        evidence_text = ""
        for i, ev in enumerate(evidence):
            scores = ev.nli_scores or {}
            ent = scores.get("entailment", 0)
            con = scores.get("contradiction", 0)
            neu = scores.get("neutral", 0)
            evidence_text += (
                f"[{i}] Source: {ev.source_title or 'Unknown'} "
                f"({ev.source_type.value}, tier: {ev.source_tier.value})\n"
                f"     URL: {ev.source_url or 'N/A'}\n"
                f"     Snippet: \"{ev.snippet[:500]}\"\n"
                f"     NLI: entailment={ent:.3f}, contradiction={con:.3f}, neutral={neu:.3f}\n\n"
            )

        if not evidence_text:
            evidence_text = "No evidence was found for this claim.\n"

        prompt = f"""You are a hallucination detection adjudicator. Given a claim extracted from an AI assistant's response and evidence pieces (each with NLI scores from a DeBERTa cross-encoder), determine whether the claim is hallucinated or factually grounded.

**LANGUAGE RULE**: Write "reasoning", "contradiction_details" and "suggestion" in the SAME language as the claim (Chinese claim → Simplified Chinese, English claim → English). JSON keys and "status" values stay in English.

## Claim
"{claim.text}"
Domain: {claim.domain.value}
Importance: {claim.importance}/1.0

## Evidence (ranked by relevance, with NLI scores)
{evidence_text}

## Instructions
1. Analyze each evidence piece in context of the claim
2. Perform multi-hop reasoning if needed (combining information from multiple evidence pieces)
3. Consider the domain-specific reliability of each source (direct_api sources like Wikipedia/PubMed are more trustworthy than web search)
4. For contradictions: assess whether they are genuine or due to different contexts/time periods/scope
5. For opinions: check if the opinion is presented as objective fact — if so, it should be flagged
6. If no evidence was found, mark as UNVERIFIED with appropriate risk score based on the claim's nature

## Risk Score Guidelines
- VERIFIED claims: 0-20 (lower = more strongly verified)
- PARTIALLY_VERIFIED claims: 20-45 (some evidence supports, some gaps)
- UNVERIFIED claims: 45-70 (no evidence found, risk depends on claim importance)
- CONTRADICTED claims: 65-100 (higher = stronger/more critical contradiction)
- OPINION claims: 5-15 (low risk unless presented as fact, then 30-50)

## Output (strict JSON only, no markdown)
{{
  "status": "VERIFIED | CONTRADICTED | UNVERIFIED | PARTIALLY_VERIFIED | OPINION",
  "risk_score": <0-100 integer>,
  "confidence": <0.0-1.0 float>,
  "reasoning": "<2-3 sentence explanation of your verdict>",
  "key_evidence_indices": [<indices of most relevant evidence pieces>],
  "contradiction_details": "<if contradicted, explain what contradicts and why — else null>",
  "suggestion": "<if uncertain, what the user should verify manually — else null>"
}}"""
        return prompt

    async def adjudicate_claim(
        self,
        claim: ExtractedClaim,
        ranked_evidence: list[EvidencePiece],
    ) -> AdjudicationResult:
        """
        Adjudicate a single claim against its ranked evidence.

        Falls back to heuristic scoring if the LLM adjudicator is unavailable.
        """
        if self.client is None:
            return self._fallback_adjudicate(claim, ranked_evidence)

        prompt = self._build_prompt(claim, ranked_evidence)

        # NLI 已高度确信时直接用启发式结论，省掉一次 10s+ 的 LLM 往返
        # （阈值参考 fallback 的 VERIFIED≥0.80 / CONTRADICTED>0.7，再收紧一点才跳过）
        if ranked_evidence:
            max_ent = max((ev.nli_scores or {}).get("entailment", 0) for ev in ranked_evidence)
            max_con = max((ev.nli_scores or {}).get("contradiction", 0) for ev in ranked_evidence)
            if max_ent >= 0.90 or max_con >= 0.85:
                return self._fallback_adjudicate(claim, ranked_evidence)
        
        max_retries = 2
        base_delay = 1.0  # seconds

        for attempt in range(max_retries):
            try:
                if self.backend == "openai":
                    resp = await asyncio.wait_for(
                        self.client.chat.completions.create(
                            model=self.model,
                            messages=[{"role": "user", "content": prompt}],
                            temperature=0.1,
                        ),
                        timeout=30.0,
                    )
                    try:
                        from app.core.provider_usage import record_provider_call
                        await record_provider_call("zen")
                    except Exception:
                        pass
                    return self._parse_response(resp.choices[0].message.content, claim, ranked_evidence)
                response = await asyncio.wait_for(
                    self.client.aio.models.generate_content(
                        model=self.model,
                        contents=prompt,
                        config={
                            "response_mime_type": "application/json",
                            "temperature": 0.1,
                        },
                    ),
                    timeout=30.0,
                )
                try:
                    from app.core.provider_usage import record_provider_call
                    await record_provider_call("gemini")
                except Exception:
                    pass
                return self._parse_response(response.text, claim, ranked_evidence)
            except Exception as e:
                error_msg = f"{type(e).__name__}: {str(e)}"
                logger.warning(f"Gemini adjudication attempt {attempt + 1}/{max_retries} failed for claim '{claim.id}': {error_msg}")
                if attempt < max_retries - 1:
                    # Check if it's a rate limit / 429 error
                    delay = base_delay * (2 ** attempt)
                    if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                        # 配额限流时退避，但封顶 8s（原来 20s+ 是速度杀手）
                        delay = min(max(delay, 8.0), 8.0)
                    logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Gemini adjudication completely failed after {max_retries} attempts.")
                    return self._fallback_adjudicate(claim, ranked_evidence)

    async def adjudicate_batch(
        self,
        claims_with_evidence: list[tuple[ExtractedClaim, list[EvidencePiece]]],
    ) -> list[AdjudicationResult]:
        """
        Adjudicate multiple claims (joint mode packs them into fewer LLM calls).

        Args:
            claims_with_evidence: List of (claim, ranked_evidence) tuples.

        Returns:
            List of AdjudicationResults in the same order.
        """
        if not claims_with_evidence:
            return []

        from app.config import get_settings as _get_settings
        mode = (_get_settings().adjudication_mode or "perclaim").strip().lower()

        # NLI 短路与无 client 的 claim 先摘除（两种模式共用，保证行为一致）
        pending: list[tuple[ExtractedClaim, list[EvidencePiece]]] = []
        results: list[Optional[AdjudicationResult]] = [None] * len(claims_with_evidence)
        for i, (claim, evidence) in enumerate(claims_with_evidence):
            if self.client is None:
                results[i] = self._fallback_adjudicate(claim, evidence)
            elif self._nli_decisive(evidence):
                results[i] = self._fallback_adjudicate(claim, evidence)
            else:
                pending.append((i, claim, evidence))

        if pending:
            if mode == "joint" and self.client is not None:
                # 每 5 个 claim 打包一次，多包之间仍并发
                chunks: list[list[tuple[int, ExtractedClaim, list[EvidencePiece]]]] = [
                    pending[j:j + 5] for j in range(0, len(pending), 5)
                ]
                chunk_outs = await asyncio.gather(
                    *(self._adjudicate_joint_chunk(ch) for ch in chunks)
                )
                for chunk, outs in zip(chunks, chunk_outs):
                    for (idx, _c, _e), out in zip(chunk, outs):
                        results[idx] = out
            else:
                semaphore = asyncio.Semaphore(5)  # Max 5 concurrent LLM calls

                async def _throttled(idx, claim: ExtractedClaim, evidence: list[EvidencePiece]):
                    async with semaphore:
                        return idx, await self.adjudicate_claim(claim, evidence)

                settled = await asyncio.gather(
                    *(_throttled(i, c, e) for i, c, e in pending),
                    return_exceptions=True,
                )
                for item in settled:
                    if isinstance(item, Exception):
                        logger.error(f"Adjudication failed: {item}")
                        continue
                    idx, res = item
                    if isinstance(res, Exception):
                        _, claim, evidence = claims_with_evidence[idx]
                        results[idx] = self._fallback_adjudicate(claim, evidence)
                    else:
                        results[idx] = res

        # 兜底：任何 außen 没填上的都走 fallback（保证数量和顺序严格对应）
        final = []
        for i, (claim, evidence) in enumerate(claims_with_evidence):
            final.append(results[i] if results[i] is not None
                         else self._fallback_adjudicate(claim, evidence))
        return final

    @staticmethod
    def _nli_decisive(evidence: list[EvidencePiece]) -> bool:
        """NLI 已高度确信时跳过 LLM（与 adjudicate_claim 内逻辑保持一致）。"""
        if not evidence:
            return False
        max_ent = max((ev.nli_scores or {}).get("entailment", 0) for ev in evidence)
        max_con = max((ev.nli_scores or {}).get("contradiction", 0) for ev in evidence)
        return max_ent >= 0.90 or max_con >= 0.85

    def _build_joint_prompt(
        self, items: list[tuple[ExtractedClaim, list[EvidencePiece]]]
    ) -> str:
        """Build one prompt judging multiple claims independently."""
        blocks = []
        for n, (claim, evidence) in enumerate(items):
            ev_lines = []
            for i, ev in enumerate(evidence):
                scores = ev.nli_scores or {}
                ev_lines.append(
                    f"[{i}] {ev.source_title or 'Unknown'} "
                    f"({ev.source_type.value}, tier: {ev.source_tier.value})\n"
                    f"    URL: {ev.source_url or 'N/A'}\n"
                    f'    "{ev.snippet[:400]}"\n'
                    f"    NLI: e={scores.get('entailment', 0):.3f}, "
                    f"c={scores.get('contradiction', 0):.3f}, "
                    f"n={scores.get('neutral', 0):.3f}"
                )
            ev_text = "\n".join(ev_lines) if ev_lines else "No evidence was found."
            blocks.append(
                f"## Claim {n}\n\"{claim.text}\"\n"
                f"Domain: {claim.domain.value}, Importance: {claim.importance}/1.0\n"
                f"Evidence:\n{ev_text}"
            )
        schema = (
            '{"status": "VERIFIED | CONTRADICTED | UNVERIFIED | PARTIALLY_VERIFIED | OPINION", '
            '"risk_score": 0-100, "confidence": 0.0-1.0, "reasoning": "...", '
            '"key_evidence_indices": [...], "contradiction_details": null, "suggestion": null}'
        )
        return (
            "You are a hallucination detection adjudicator. "
            "Judge EACH numbered claim INDEPENDENTLY against ONLY its own evidence list.\n"
            "LANGUAGE RULE per claim: write reasoning/contradiction_details/suggestion "
            "in the SAME language as that claim. JSON keys and status values stay in English.\n"
            "Risk guidelines: VERIFIED 0-20, PARTIALLY_VERIFIED 20-45, "
            "UNVERIFIED 45-70, CONTRADICTED 65-100, OPINION 5-15.\n\n"
            + "\n\n".join(blocks)
            + f"\n\nOutput strict JSON only, {len(items)} items in input order:\n"
            + '{"results": [' + ", ".join([schema] * len(items)) + "]}"
        )

    async def _call_llm_text(self, prompt: str) -> str:
        """One LLM call, returns raw text (both backends)."""
        if self.backend == "openai":
            resp = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                ),
                timeout=40.0,
            )
            try:
                from app.core.provider_usage import record_provider_call
                await record_provider_call("zen")
            except Exception:
                pass
            return resp.choices[0].message.content
        response = await asyncio.wait_for(
            self.client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0.1},
            ),
            timeout=40.0,
        )
        try:
            from app.core.provider_usage import record_provider_call
            await record_provider_call("gemini")
        except Exception:
            pass
        return response.text

    async def _adjudicate_joint_chunk(
        self, chunk: list[tuple[int, ExtractedClaim, list[EvidencePiece]]]
    ) -> list[AdjudicationResult]:
        """Judge one chunk (≤5 claims) with a single LLM call."""
        items = [(c, e) for _, c, e in chunk]
        text = None
        for attempt in range(2):
            try:
                text = await self._call_llm_text(self._build_joint_prompt(items))
                break
            except Exception as e:
                logger.warning(
                    f"Joint adjudication attempt {attempt + 1}/2 failed "
                    f"({type(e).__name__}: {e})"
                )
        if text is None:
            logger.warning("Joint adjudication failed, heuristic fallback")
            return [self._fallback_adjudicate(c, e) for _, c, e in chunk]
        try:
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
            data = json.loads(cleaned.strip())
            arr = data.get("results", data) if isinstance(data, dict) else data
            if not isinstance(arr, list):
                raise ValueError("joint response has no results list")
        except Exception as e:
            logger.warning(f"Joint response parse failed, heuristic fallback: {e}")
            return [self._fallback_adjudicate(c, e) for _, c, e in chunk]

        outs = []
        for (_, claim, evidence), entry in zip(chunk, arr):
            try:
                outs.append(self._build_result_from_data(claim, entry))
            except Exception as e:
                logger.warning(f"Joint item parse failed for {claim.id}: {e}")
                outs.append(self._fallback_adjudicate(claim, evidence))
        # 模型漏项时补齐
        while len(outs) < len(chunk):
            _, claim, evidence = chunk[len(outs)]
            outs.append(self._fallback_adjudicate(claim, evidence))
        return outs

    def _parse_response(self, response_text: str, claim: ExtractedClaim, evidence: list[EvidencePiece] | None = None) -> AdjudicationResult:
        """Parse Gemini's JSON response into an AdjudicationResult."""
        try:
            # Clean potential markdown wrapping
            text = response_text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            data = json.loads(text)

            return self._build_result_from_data(claim, data)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"Failed to parse Gemini response: {e}\nRaw: {response_text[:500]}")
            # 修复：原来传 [] 会丢掉全部证据导致结论变 UNVERIFIED；现在保留已排序证据
            return self._fallback_adjudicate(claim, evidence or [])

    @staticmethod
    def _build_result_from_data(claim: ExtractedClaim, data: dict) -> AdjudicationResult:
        """Build an AdjudicationResult from one parsed JSON object (single & joint share)."""
        status_map = {
            "VERIFIED": ClaimStatus.VERIFIED,
            "PARTIALLY_VERIFIED": ClaimStatus.PARTIALLY_VERIFIED,
            "UNVERIFIED": ClaimStatus.UNVERIFIED,
            "CONTRADICTED": ClaimStatus.CONTRADICTED,
            "OPINION": ClaimStatus.OPINION,
        }
        status = status_map.get(
            str(data.get("status", "UNVERIFIED")).strip().upper(),
            ClaimStatus.UNVERIFIED,
        )
        return AdjudicationResult(
            claim=claim,
            status=status,
            risk_score=float(data.get("risk_score", 50)),
            confidence=float(data.get("confidence", 0.5)),
            reasoning=data.get("reasoning", "No reasoning provided"),
            key_evidence_indices=data.get("key_evidence_indices", []),
            contradiction_details=data.get("contradiction_details"),
            suggestion=data.get("suggestion"),
        )

    def _fallback_adjudicate(
        self,
        claim: ExtractedClaim,
        evidence: list[EvidencePiece],
    ) -> AdjudicationResult:
        """
        Heuristic fallback when the LLM adjudicator is unavailable or NLI is decisive.

        Uses NLI scores directly (similar to V1 logic but simplified).
        """
        if not evidence:
            # No evidence = unverified
            risk = 55.0 if claim.importance > 0.5 else 45.0
            return AdjudicationResult(
                claim=claim,
                status=ClaimStatus.UNVERIFIED,
                risk_score=risk,
                confidence=0.3,
                reasoning="No evidence found. Unable to verify this claim.",
                key_evidence_indices=[],
                contradiction_details=None,
                suggestion="Try searching for this claim manually.",
            )

        max_ent = max((ev.nli_scores or {}).get("entailment", 0) for ev in evidence)
        max_con = max((ev.nli_scores or {}).get("contradiction", 0) for ev in evidence)

        if claim.domain == ClaimDomain.OPINION_SUBJECTIVE:
            return AdjudicationResult(
                claim=claim,
                status=ClaimStatus.OPINION,
                risk_score=10.0,
                confidence=0.7,
                reasoning="This is an opinion/subjective claim.",
                key_evidence_indices=[],
                contradiction_details=None,
                suggestion=None,
            )
        
        # If we have a very strong entailment score from any chunk, it's VERIFIED
        if max_ent >= 0.80:
            status = ClaimStatus.VERIFIED
            risk = max(5.0, 30.0 * (1.0 - max_ent))
        # Otherwise, if we have a strong contradiction, it's CONTRADICTED
        elif max_con > 0.7:
            status = ClaimStatus.CONTRADICTED
            risk = 70.0 + (max_con - 0.7) * 100
            risk = min(risk, 95.0)
        # Moderate entailment -> Partially Verified
        elif max_ent > 0.5:
            status = ClaimStatus.PARTIALLY_VERIFIED
            risk = 30.0 + (1.0 - max_ent) * 30
        else:
            status = ClaimStatus.UNVERIFIED
            risk = 50.0 + claim.importance * 15

        return AdjudicationResult(
            claim=claim,
            status=status,
            risk_score=round(risk, 1),
            confidence=max(max_ent, max_con, 0.3),
            reasoning=f"Fallback heuristic: max_entailment={max_ent:.3f}, max_contradiction={max_con:.3f}",
            key_evidence_indices=list(range(min(3, len(evidence)))),
            contradiction_details=f"Max contradiction score: {max_con:.3f}" if max_con > 0.5 else None,
            suggestion=("LLM adjudicator not configured, verify manually using the cited sources." if self.backend == "none" else "NLI evidence decisive, LLM adjudication skipped to save time; verify manually if in doubt.") if max_ent < 0.5 else None,
        )
