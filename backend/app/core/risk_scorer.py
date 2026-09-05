"""
Hallucination risk score calculator — V2.

The per-claim risk score is now computed by the LLM adjudicator (Gemini 3 Flash).
This module handles:
1. Message-level risk aggregation from adjudicated claim scores
2. Risk level / color / warning message mapping
3. Contextual warning generation using adjudication reasoning
"""

import logging
from typing import Optional

from app.models.detect import (
    ClaimVerificationResult,
    ClaimStatus,
    ClaimDomain,
    RiskLevel,
    Warning,
)

logger = logging.getLogger(__name__)


class RiskScorer:
    """
    V2 Risk Scorer — aggregates LLM-adjudicated per-claim risk scores
    into an overall message risk score.

    The old _compute_claim_risk() weighted formula is replaced by
    the ClaimAdjudicator. This class now focuses on:
    - Message-level aggregation with hard floors and ratio penalties
    - Risk level mapping
    - Warning generation with adjudication reasoning
    """

    def compute_overall_risk(
        self, results: list[ClaimVerificationResult]
    ) -> float:
        """
        Aggregate per-claim risk scores into an overall message risk score.

        Formula:
            base_score = importance-weighted average of claim risk_scores
        
        Adjustments:
            - Any high-confidence CONTRADICTED → floor at 65
            - >50% UNVERIFIED → floor at 55
            - >30% CONTRADICTED ratio → +15
            - >60% UNVERIFIED ratio → +10
            - All VERIFIED → cap at 20
        """
        # Filter to scoreable claims (exclude OPINION, SKIPPED).
        # UNVERIFIABLE_SOURCE ("判不了", e.g. source link 403) is abstained:
        # it only surfaces as a warning, never inflates the numeric score
        # (same idea as RefChecker's Abstain category).
        scoreable = [
            r for r in results
            if r.status not in (
                ClaimStatus.SKIPPED,
                ClaimStatus.OPINION,
                ClaimStatus.UNVERIFIABLE_SOURCE,
            )
        ]
        unverifiable = [
            r for r in results if r.status == ClaimStatus.UNVERIFIABLE_SOURCE
        ]

        if not scoreable:
            return 55.0 if unverifiable else 0.0

        # Weighted average by importance
        total_weight = sum(r.claim.importance for r in scoreable)
        if total_weight == 0:
            total_weight = len(scoreable)
            weighted_sum = sum(r.risk_score for r in scoreable)
        else:
            weighted_sum = sum(
                r.risk_score * r.claim.importance for r in scoreable
            )

        base_score = weighted_sum / total_weight

        # Categorize results
        contradicted = [r for r in scoreable if r.status == ClaimStatus.CONTRADICTED]
        unverified = [r for r in scoreable if r.status == ClaimStatus.UNVERIFIED]
        verified = [
            r for r in scoreable
            if r.status in (ClaimStatus.VERIFIED, ClaimStatus.PARTIALLY_VERIFIED)
        ]

        c_ratio = len(contradicted) / len(scoreable)
        u_ratio = len(unverified) / len(scoreable)

        # Hard floor: any high-confidence contradiction → minimum 65
        if any(r.confidence > 0.8 and r.status == ClaimStatus.CONTRADICTED for r in scoreable):
            base_score = max(base_score, 65.0)

        # Hard floor: majority unverified → minimum 55
        if u_ratio > 0.5:
            base_score = max(base_score, 55.0)

        # Ratio penalties
        if c_ratio > 0.3:
            base_score += 15.0
        if u_ratio > 0.6:
            base_score += 10.0

        # All verified cap
        if len(verified) == len(scoreable) and len(verified) > 0:
            base_score = min(base_score, 20.0)

        return round(min(max(base_score, 0.0), 100.0), 1)

    def get_risk_level(self, score: float) -> RiskLevel:
        """Map a risk score to a risk level."""
        if score <= 25:
            return RiskLevel.LOW
        elif score <= 50:
            return RiskLevel.MODERATE
        elif score <= 75:
            return RiskLevel.HIGH
        else:
            return RiskLevel.CRITICAL

    def get_risk_color(self, level: RiskLevel) -> str:
        """Get hex color for a risk level."""
        colors = {
            RiskLevel.LOW: "#22C55E",       # Green
            RiskLevel.MODERATE: "#EAB308",   # Amber
            RiskLevel.HIGH: "#F97316",       # Orange
            RiskLevel.CRITICAL: "#EF4444",   # Red
        }
        return colors.get(level, "#6B7280")

    def get_warning_message(self, level: RiskLevel) -> str:
        """Get the default warning message for a risk level."""
        messages = {
            RiskLevel.LOW: "Response appears well-grounded",
            RiskLevel.MODERATE: "Some claims could not be fully verified",
            RiskLevel.HIGH: "Multiple unverified or questionable claims detected",
            RiskLevel.CRITICAL: "Response contains likely hallucinated content",
        }
        return messages.get(level, "Unknown risk level")

    def generate_warnings(
        self, results: list[ClaimVerificationResult]
    ) -> list[Warning]:
        """Generate contextual warnings using adjudication reasoning."""
        warnings = []

        for result in results:
            if result.status == ClaimStatus.SKIPPED:
                continue

            # Source unreachable — abstained from scoring, warn only
            if result.status == ClaimStatus.UNVERIFIABLE_SOURCE:
                warnings.append(Warning(
                    type="unverifiable_source",
                    message=f'Source unreachable, could not verify: "{result.claim.text[:100]}"',
                    claim_id=result.claim.id,
                    source_url=(result.evidence[0].source_url if result.evidence else None),
                ))

            # No evidence found
            elif not result.evidence and result.status == ClaimStatus.UNVERIFIED:
                warnings.append(Warning(
                    type="no_source",
                    message=f'No verifiable source found for: "{result.claim.text[:100]}"',
                    claim_id=result.claim.id,
                ))

            # Contradiction detected — use adjudicator reasoning
            elif result.status == ClaimStatus.CONTRADICTED:
                if result.contradiction_details:
                    msg = result.contradiction_details[:200]
                else:
                    msg = f'Contradicted: "{result.claim.text[:80]}"'
                # Find the contradicting source
                source_url = None
                for ev in result.evidence:
                    if ev.nli_scores and ev.nli_scores.get("contradiction", 0) > 0.7:
                        source_url = ev.source_url
                        break
                warnings.append(Warning(
                    type="contradiction",
                    message=msg,
                    claim_id=result.claim.id,
                    source_url=source_url,
                ))

            # Numerical/Statistical claim with low support
            elif (
                result.claim.domain in (
                    ClaimDomain.NUMERICAL_STATISTICAL,
                    ClaimDomain.FINANCE_BUSINESS,
                )
                and result.max_entailment_score < 0.5
                and result.status == ClaimStatus.UNVERIFIED
            ):
                warnings.append(Warning(
                    type="unverified_statistic",
                    message=f'Statistical/financial claim could not be verified: "{result.claim.text[:100]}"',
                    claim_id=result.claim.id,
                ))

            # Opinion presented as fact
            elif result.status == ClaimStatus.OPINION and result.risk_score > 25:
                warnings.append(Warning(
                    type="opinion_as_fact",
                    message=f'Opinion may be presented as fact: "{result.claim.text[:100]}"',
                    claim_id=result.claim.id,
                ))

            # Sources disagree
            elif result.source_agreement_variance > 0.3:
                warnings.append(Warning(
                    type="source_disagreement",
                    message=f'Sources disagree on: "{result.claim.text[:80]}" — check linked sources',
                    claim_id=result.claim.id,
                ))

        return warnings


# ── Module-level singleton ────────────────────────────────────────────────

_scorer: Optional[RiskScorer] = None


def get_risk_scorer() -> RiskScorer:
    """Get or create the risk scorer singleton."""
    global _scorer
    if _scorer is None:
        _scorer = RiskScorer()
    return _scorer
