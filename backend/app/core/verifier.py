"""
Multi-source verification orchestrator — V2.

Coordinates the full verification pipeline:
1. Domain-routed evidence gathering (free APIs + filtered Tavily/Serper)
2. NLI scoring on all (claim, evidence) pairs
3. Evidence ranking by informativeness + source tier
4. LLM adjudication via Gemini 3 Flash
5. Assembly of per-claim verification results

Also retains conversation history (NER) and vector DB checks as
additional evidence sources alongside the domain-specific APIs.
"""

import asyncio
import logging
from typing import Optional

import numpy as np

from app.config import get_settings
from app.models.detect import (
    ExtractedClaim,
    EvidencePiece,
    ClaimVerificationResult,
    ClaimStatus,
    ClaimDomain,
    NLILabel,
    SourceType,
    SourceTier,
    ConversationMessage,
)
from app.core.nli_model import get_nli_model
from app.core.web_search import get_web_searcher
from app.core.ner_extractor import NERResult
from app.core.vector_db import semantic_search_documents
from app.core.domain_source_router import get_domain_source_router
from app.core.evidence_ranker import rank_evidence
from app.core.claim_adjudicator import get_claim_adjudicator

logger = logging.getLogger(__name__)


class ClaimVerifier:
    """
    V2 Verification Orchestrator.

    For each claim:
    1. Gather evidence from domain-specific sources (via DomainSourceRouter)
       + conversation history (NER) + vector DB (documents)
    2. Run NLI on all (claim, evidence) pairs in batch
    3. Rank evidence by informativeness + source tier
    4. Send claim + top-K evidence to LLM adjudicator
    5. Assemble final ClaimVerificationResult with adjudication verdict
    """

    def __init__(self):
        self.settings = get_settings()
        self.nli_model = get_nli_model()
        self.domain_router = get_domain_source_router()
        self.adjudicator = get_claim_adjudicator()
        # Keep web_searcher for direct citation URL fetching
        self.web_searcher = get_web_searcher()

    async def verify_claims(
        self,
        claims: list[ExtractedClaim],
        conversation_history: Optional[list[ConversationMessage]] = None,
        ner_result: Optional[NERResult] = None,
        document_ids: Optional[list[str]] = None,
        config: Optional[dict] = None,
        platform_sources: Optional[list] = None,
    ) -> list[ClaimVerificationResult]:
        """
        Verify a list of claims through the full V2 pipeline.

        Pipeline:
            Extract claims → Gather evidence (domain-routed) →
            NLI scoring → Evidence ranking → LLM adjudication →
            Assemble results

        Args:
            claims: Extracted claims to verify.
            conversation_history: Conversation messages for context checks.
            ner_result: NER extraction result for entity graph queries.
            document_ids: IDs of user-uploaded documents.
            config: Detection config overrides.
            platform_sources: Web links from extensions (e.g. ChatGPT citations).

        Returns:
            List of ClaimVerificationResult for each claim.
        """
        if not claims:
            return []

        threshold = (
            config.get("claim_threshold", self.settings.claim_confidence_threshold)
            if config else self.settings.claim_confidence_threshold
        )
        check_conv = config.get("check_conversation", True) if config else True
        check_docs = config.get("check_documents", True) if config else True

        # These domains are too critical to skip purely based on extractor confidence.
        always_verify_domains = {
            ClaimDomain.NUMERICAL_STATISTICAL,
            ClaimDomain.FINANCE_BUSINESS,
            ClaimDomain.MEDICAL_HEALTH,
            ClaimDomain.LEGAL_REGULATORY,
            ClaimDomain.HISTORICAL,
            ClaimDomain.NEWS_CURRENT_EVENTS,
        }

        def should_verify_claim(claim: ExtractedClaim) -> bool:
            if claim.domain in always_verify_domains:
                return True
            if claim.citation_indices:
                return True
            if claim.importance >= 0.55:
                return True
            return claim.confidence_needs_checking >= threshold

        # ── Stage 1: Gather evidence for all claims in parallel ───────
        evidence_tasks = []
        claim_indices = []  # Track which claims are being verified vs skipped

        for i, claim in enumerate(claims):
            if not should_verify_claim(claim):
                logger.debug(
                    "Skipping claim %s: confidence=%s threshold=%s domain=%s importance=%s",
                    claim.id,
                    claim.confidence_needs_checking,
                    threshold,
                    claim.domain.value if hasattr(claim.domain, "value") else claim.domain,
                    claim.importance,
                )
                continue

            claim_indices.append(i)
            evidence_tasks.append(
                self._gather_all_evidence(
                    claim=claim,
                    conversation_history=conversation_history,
                    ner_result=ner_result,
                    document_ids=document_ids,
                    check_conv=check_conv,
                    check_docs=check_docs,
                    platform_sources=platform_sources,
                )
            )

        # Run all evidence gathering in parallel
        evidence_results = await asyncio.gather(*evidence_tasks, return_exceptions=True)

        # ── Stage 2: Run NLI on all (claim, evidence) pairs ───────────
        all_nli_pairs = []
        pair_mapping = []  # (result_idx, claim_idx, ev_idx)

        for result_idx, (claim_idx, evidence_result) in enumerate(
            zip(claim_indices, evidence_results)
        ):
            if isinstance(evidence_result, Exception):
                logger.error(
                    f"Evidence gathering failed for claim "
                    f"{claims[claim_idx].id}: {evidence_result}"
                )
                continue

            evidence_list, sources_checked = evidence_result
            for ev_idx, evidence in enumerate(evidence_list):
                all_nli_pairs.append((evidence.snippet, claims[claim_idx].verification_text))
                pair_mapping.append((result_idx, claim_idx, ev_idx))

        # Batch NLI inference
        nli_results = []
        if all_nli_pairs and self.nli_model.is_loaded:
            try:
                nli_results = await self.nli_model.predict(all_nli_pairs)
            except Exception as e:
                logger.error(f"NLI batch inference failed: {e}")
                nli_results = [
                    {"entailment": 0.0, "contradiction": 0.0, "neutral": 1.0}
                ] * len(all_nli_pairs)

        # Map NLI results back to evidence pieces
        nli_idx = 0
        evidence_by_claim: dict[int, tuple[list[EvidencePiece], list[SourceType]]] = {}

        for result_idx, (claim_idx, evidence_result) in enumerate(
            zip(claim_indices, evidence_results)
        ):
            if isinstance(evidence_result, Exception):
                evidence_by_claim[claim_idx] = ([], [])
                continue

            evidence_list, sources_checked = evidence_result
            for ev_idx, evidence in enumerate(evidence_list):
                if nli_idx < len(nli_results):
                    scores = nli_results[nli_idx]
                    evidence.nli_scores = scores
                    evidence.nli_label = NLILabel(self.nli_model.get_label(scores))
                    nli_idx += 1

            evidence_by_claim[claim_idx] = (evidence_list, sources_checked)

        # ── Stage 3: Rank evidence + Stage 4: LLM Adjudication ────────
        max_ev = self.settings.max_evidence_per_claim
        min_info = self.settings.min_evidence_informativeness

        # Prepare adjudication batch
        claims_for_adjudication = []
        ranked_evidence_map = {}
        nli_stats_map = {}

        for claim_idx in claim_indices:
            claim = claims[claim_idx]
            evidence_list, sources_checked = evidence_by_claim.get(claim_idx, ([], []))

            # Rank evidence for this claim
            ranked = rank_evidence(evidence_list, max_count=max_ev, min_informativeness=min_info)
            ranked_evidence_map[claim_idx] = (ranked, evidence_list, sources_checked)

            # Compute NLI stats for the result
            ent_scores = [
                (ev.nli_scores or {}).get("entailment", 0) for ev in evidence_list
            ]
            con_scores = [
                (ev.nli_scores or {}).get("contradiction", 0) for ev in evidence_list
            ]
            nli_stats_map[claim_idx] = {
                "max_ent": max(ent_scores) if ent_scores else 0.0,
                "max_con": max(con_scores) if con_scores else 0.0,
                "agreement_var": float(np.var(ent_scores)) if len(ent_scores) > 1 else 0.0,
            }

            claims_for_adjudication.append((claim, ranked))

        # Batch adjudication
        adjudication_results = []
        if claims_for_adjudication:
            try:
                adjudication_results = await self.adjudicator.adjudicate_batch(
                    claims_for_adjudication
                )
            except Exception as e:
                logger.error(f"Batch adjudication failed: {e}")
                # Fallback: create placeholder results
                adjudication_results = []
                for claim, evidence in claims_for_adjudication:
                    adjudication_results.append(
                        self.adjudicator._fallback_adjudicate(claim, evidence)
                    )

        # ── Stage 5: Assemble final results ───────────────────────────
        adj_map = {}
        for adj_result in adjudication_results:
            adj_map[adj_result.claim.id] = adj_result

        results = []
        for i, claim in enumerate(claims):
            if not should_verify_claim(claim):
                # Skipped claim
                results.append(ClaimVerificationResult(
                    claim=claim,
                    risk_score=0,
                    status=ClaimStatus.SKIPPED,
                ))
                continue

            ranked_ev, all_ev, sources_checked = ranked_evidence_map.get(i, ([], [], []))
            stats = nli_stats_map.get(i, {"max_ent": 0, "max_con": 0, "agreement_var": 0})
            adj = adj_map.get(claim.id)

            # Source coverage
            sources_with_evidence = set(ev.source_type for ev in all_ev)
            source_coverage = (
                len(sources_with_evidence) / len(sources_checked)
                if sources_checked else 0.0
            )

            if adj:
                result = ClaimVerificationResult(
                    claim=claim,
                    risk_score=adj.risk_score,
                    status=adj.status,
                    confidence=adj.confidence,
                    reasoning=adj.reasoning,
                    suggestion=adj.suggestion,
                    contradiction_details=adj.contradiction_details,
                    max_entailment_score=stats["max_ent"],
                    max_contradiction_score=stats["max_con"],
                    source_coverage=source_coverage,
                    source_agreement_variance=stats["agreement_var"],
                    evidence=ranked_ev,  # Send only ranked evidence
                    sources_checked=sources_checked,
                )
            else:
                # No adjudication result — fallback
                result = self._build_fallback_result(
                    claim, all_ev, sources_checked, stats
                )

            results.append(result)

        return results

    async def _gather_all_evidence(
        self,
        claim: ExtractedClaim,
        conversation_history: Optional[list[ConversationMessage]],
        ner_result: Optional[NERResult],
        document_ids: Optional[list[str]],
        check_conv: bool,
        check_docs: bool,
        platform_sources: Optional[list] = None,
    ) -> tuple[list[EvidencePiece], list[SourceType]]:
        """
        Gather evidence from ALL sources for a single claim:
        1. Domain-specific APIs + filtered Tavily/Serper (via DomainSourceRouter)
        2. Conversation history (NER entity graph)
        3. Vector DB (user-uploaded documents)
        4. Direct citation URLs (from extension platform sources)
        """
        tasks = []
        source_types = []

        # 1. Domain-routed sources (APIs + filtered Tavily/Serper)
        tasks.append(self.domain_router.gather_evidence(claim))
        source_types.append([SourceType.WEB_SEARCH, SourceType.DIRECT_API])

        # 2. Conversation history — check if context exists
        if check_conv and conversation_history and ner_result:
            tasks.append(self._check_conversation(claim, conversation_history, ner_result))
            source_types.append([SourceType.CONVERSATION_HISTORY])

        # 3. Documents — check if docs exist
        if check_docs and document_ids:
            tasks.append(self._check_vector_db(claim, document_ids))
            source_types.append([SourceType.VECTOR_DB])

        # 4. Direct citation URLs from extension
        if platform_sources and claim.citation_indices:
            direct_urls = []
            for index in claim.citation_indices:
                matching_source = next(
                    (s for s in platform_sources if getattr(s, "index", None) == index),
                    None,
                )
                if matching_source and matching_source.url:
                    direct_urls.append(matching_source.url)

            for url in set(direct_urls):
                logger.info(f"Direct citation match for claim {claim.id}: {url}")
                tasks.append(self.web_searcher.fetch_url_content(url))
                source_types.append([SourceType.WEB_SEARCH])

        if not tasks:
            return [], []

        # Run all source checks in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_evidence = []
        sources_checked = []
        for keys, result in zip(source_types, results):
            if isinstance(result, Exception):
                logger.error(f"Source check failed for {keys}: {result}")
                continue
            
            sources_checked.extend(keys)
            
            if isinstance(result, list):
                all_evidence.extend(result)
            elif result is not None:
                all_evidence.append(result)

        return all_evidence, list(set(sources_checked))

    async def _check_conversation(
        self,
        claim: ExtractedClaim,
        conversation_history: list[ConversationMessage],
        ner_result: NERResult,
    ) -> list[EvidencePiece]:
        """Check claim against conversation history using NER entities."""
        evidence = []

        context_items = ner_result.get_context_around_entities(
            claim.key_entities, conversation_history
        )

        for msg_idx, msg_content in context_items[:3]:
            evidence.append(EvidencePiece(
                source_type=SourceType.CONVERSATION_HISTORY,
                source_tier=SourceTier.CONVERSATION,
                message_index=msg_idx,
                snippet=msg_content[:500],
                source_title=f"Conversation message #{msg_idx + 1}",
            ))

        return evidence

    async def _check_vector_db(
        self,
        claim: ExtractedClaim,
        document_ids: list[str],
    ) -> list[EvidencePiece]:
        """Check claim against pre-embedded documents using pgvector."""
        evidence = []

        results = await semantic_search_documents(
            query_text=claim.verification_text,
            document_ids=document_ids,
            top_k=3,
        )

        for res in results:
            evidence.append(EvidencePiece(
                source_type=SourceType.VECTOR_DB,
                source_tier=SourceTier.VECTOR_DB,
                document_name=res["document_name"],
                chunk_index=res["chunk_index"],
                snippet=res["text_content"],
                source_title=f"Doc chunk from {res['document_name']} (Chunk {res['chunk_index']})",
            ))

        return evidence

    def _build_fallback_result(
        self,
        claim: ExtractedClaim,
        evidence: list[EvidencePiece],
        sources_checked: list[SourceType],
        stats: dict,
    ) -> ClaimVerificationResult:
        """Build a result when adjudication is unavailable (fallback)."""
        if not evidence:
            is_blocked_url = bool(
                claim.citation_indices and SourceType.WEB_SEARCH in sources_checked
            )
            return ClaimVerificationResult(
                claim=claim,
                risk_score=60.0 if is_blocked_url else 65.0,
                status=ClaimStatus.UNVERIFIABLE_SOURCE if is_blocked_url else ClaimStatus.UNVERIFIED,
                sources_checked=sources_checked,
                source_coverage=0.0,
            )

        max_ent = stats["max_ent"]
        max_con = stats["max_con"]

        if max_con > 0.7:
            status = ClaimStatus.CONTRADICTED
        elif max_ent > 0.6:
            status = ClaimStatus.VERIFIED
        else:
            status = ClaimStatus.UNVERIFIED

        sources_with_evidence = set(ev.source_type for ev in evidence)
        source_coverage = (
            len(sources_with_evidence) / len(sources_checked)
            if sources_checked else 0.0
        )

        return ClaimVerificationResult(
            claim=claim,
            status=status,
            risk_score=50.0,  # Placeholder score when adjudicator unavailable
            reasoning="Adjudicator unavailable — heuristic verdict based on NLI scores",
            max_entailment_score=max_ent,
            max_contradiction_score=max_con,
            source_coverage=source_coverage,
            source_agreement_variance=stats["agreement_var"],
            evidence=evidence,
            sources_checked=sources_checked,
        )


# ── Module-level singleton ────────────────────────────────────────────────

_verifier: Optional[ClaimVerifier] = None


def get_claim_verifier() -> ClaimVerifier:
    """Get or create the claim verifier singleton."""
    global _verifier
    if _verifier is None:
        _verifier = ClaimVerifier()
    return _verifier
