"""
Detection API endpoint.

POST /api/v1/detect — Unified hallucination detection pipeline.

Supports two modes (auto-detected from request body):
1. SINGLE MODE (frontend): model_response + conversation_history
2. EXTENSION MODE (browser extensions): platform + conversation + messages[]
"""

import asyncio
import time
import uuid
import logging

from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.apikey_auth import require_api_key_if_configured

from app.models.detect import (
    DetectionRequest,
    DetectionResponse,
    DetectionMetadata,
    ClaimResultResponse,
    ClaimDomain,
    ClaimStatus,
    ConversationMessage,
    HighlightClaim,
    MessageDetectionResult,
    RiskLevel,
    ExtensionSource,
)
from app.core.claim_extractor import get_claim_extractor
from app.core.ner_extractor import get_ner_extractor
from app.core.verifier import get_claim_verifier
from app.core.risk_scorer import get_risk_scorer
from app.db.engine import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter()


def _map_claim_status_from_verdict(verdict: str | None) -> ClaimStatus:
    """Normalize persisted verdict strings to API status enum values."""
    normalized = (verdict or "").strip().upper()

    if normalized in {"SUPPORTED", "VERIFIED", "ENTAILMENT"}:
        return ClaimStatus.VERIFIED
    if normalized in {"CONTRADICTED", "CONTRADICTION"}:
        return ClaimStatus.CONTRADICTED
    if normalized in {"PARTIALLY_VERIFIED", "PARTIALLY_SUPPORTED", "PARTIAL"}:
        return ClaimStatus.PARTIALLY_VERIFIED
    if normalized in {"OPINION", "SUBJECTIVE"}:
        return ClaimStatus.OPINION
    if normalized == "UNVERIFIABLE_SOURCE":
        return ClaimStatus.UNVERIFIABLE_SOURCE
    if normalized == "SKIPPED":
        return ClaimStatus.SKIPPED

    return ClaimStatus.UNVERIFIED


def _map_claim_domain(raw_claim_domain) -> ClaimDomain:
    """Map DB claim domain values to ClaimDomain enum safely."""
    if isinstance(raw_claim_domain, ClaimDomain):
        return raw_claim_domain

    if raw_claim_domain is None:
        return ClaimDomain.GENERAL_FACTUAL

    text = str(raw_claim_domain).strip()
    if not text:
        return ClaimDomain.GENERAL_FACTUAL

    # Accept enum names (GENERAL_FACTUAL) and enum values (general_factual)
    by_name = text.upper()
    if by_name in ClaimDomain.__members__:
        return ClaimDomain[by_name]

    by_value = text.lower()
    for claim_domain in ClaimDomain:
        if claim_domain.value == by_value:
            return claim_domain

    return ClaimDomain.GENERAL_FACTUAL


def _normalize_claim_text_for_key(value: str | None) -> str:
    return " ".join((value or "").split()).strip().lower()


def _build_claim_response_dedupe_key(claim_response: ClaimResultResponse) -> str:
    domain_value = (
        claim_response.domain.value
        if hasattr(claim_response.domain, "value")
        else str(claim_response.domain or "")
    )
    text_value = _normalize_claim_text_for_key(
        claim_response.exact_quote or claim_response.text
    )
    return f"{domain_value}::{text_value}"


def _build_message_result_key(
    message_id: str | None,
    message_index: int | None,
    assistant_role_index: int | None,
) -> str | None:
    if message_id:
        return f"id:{message_id}"
    if assistant_role_index is not None:
        return f"assistant_role_index:{assistant_role_index}"
    if message_index is not None:
        return f"message_index:{message_index}"
    return None


# ── Single-message detection (existing pipeline — used by frontend) ──────

async def _run_detection_pipeline(
    model_response: str,
    conversation_history: list[ConversationMessage],
    conversation_id: str | None,
    document_ids: list[str],
    config: dict,
    platform_sources: list[ExtensionSource] | None = None,
) -> dict:
    """
    Core detection pipeline for a single AI response.
    Returns raw verification results + metadata.
    """
    claim_extractor = get_claim_extractor()
    ner_extractor = get_ner_extractor()

    has_documents = len(document_ids) > 0

    # Run NER and claim extraction in parallel
    ner_messages = [
        ConversationMessage(role="assistant", content=model_response)
    ]
    ner_task = ner_extractor.extract(
        messages=ner_messages,
        conversation_id=conversation_id
    )
    claims_task = claim_extractor.extract_claims(
        ai_response=model_response,
        conversation_history=conversation_history,
        has_documents=has_documents,
    )

    ner_result, extracted_claims = await asyncio.gather(
        ner_task, claims_task
    )

    logger.info(f"Extracted {len(extracted_claims)} claims, "
                f"{len(ner_result.entities)} NER entities")

    # Multi-source verification + NLI
    verifier = get_claim_verifier()
    verification_results = await verifier.verify_claims(
        claims=extracted_claims,
        conversation_history=conversation_history or None,
        ner_result=ner_result,
        document_ids=document_ids or None,
        config=config,
        platform_sources=platform_sources,
    )

    # Risk score aggregation
    scorer = get_risk_scorer()
    overall_risk = scorer.compute_overall_risk(verification_results)
    risk_level = scorer.get_risk_level(overall_risk)
    risk_color = scorer.get_risk_color(risk_level)
    warning_message = scorer.get_warning_message(risk_level)
    warnings = scorer.generate_warnings(verification_results)

    return {
        "verification_results": verification_results,
        "extracted_claims": extracted_claims,
        "overall_risk": overall_risk,
        "risk_level": risk_level,
        "risk_color": risk_color,
        "warning_message": warning_message,
        "warnings": warnings,
    }


def _build_claims_response(verification_results) -> list[ClaimResultResponse]:
    claims_response = []
    for result in verification_results:
        max_neutral = 0.0
        for ev in result.evidence:
            if ev.nli_scores and "neutral" in ev.nli_scores:
                max_neutral = max(max_neutral, ev.nli_scores["neutral"])
                
        verification_details = {
            "entailment_score": result.max_entailment_score,
            "contradiction_score": result.max_contradiction_score,
            "neutral_score": max_neutral,
            "sources_checked": [s.value for s in result.sources_checked],
            "evidence": [
                {
                    "source_type": ev.source_type.value,
                    "source_tier": ev.source_tier.value if hasattr(ev, "source_tier") and ev.source_tier else None,
                    "source_url": ev.source_url,
                    "source_title": ev.source_title,
                    "document_name": ev.document_name,
                    "chunk_index": ev.chunk_index,
                    "message_index": ev.message_index,
                    "snippet": ev.snippet[:300],
                    "nli_label": ev.nli_label.value if ev.nli_label else None,
                    "nli_scores": ev.nli_scores,
                }
                for ev in result.evidence
            ],
        }

        # Build explanation note prioritizing adjudicator reasoning
        status_text = result.status.value if hasattr(result.status, 'value') else str(result.status)
        note_parts = [f"Status: {status_text}"]
        
        if result.reasoning:
            note_parts.append(result.reasoning)
        elif result.status == ClaimStatus.UNVERIFIABLE_SOURCE:
            note_parts.append("Source link unreachable (e.g. 403 Forbidden). Could not verify.")
        elif result.max_contradiction_score > 0.3:
            note_parts.append(f"Contradiction detected (score: {result.max_contradiction_score:.2f})")
        elif result.max_entailment_score > 0.7:
            note_parts.append(f"Strongly supported (score: {result.max_entailment_score:.2f})")

        # Collect citation URLs/names
        citations = []
        for ev in result.evidence:
            if ev.source_url:
                citations.append(ev.source_url)
            elif ev.source_title:
                citations.append(ev.source_title)
            elif ev.document_name:
                citations.append(ev.document_name)

        claims_response.append(ClaimResultResponse(
            id=result.claim.id,
            text=result.claim.text,
            exact_quote=getattr(result.claim, "exact_quote", None),
            domain=getattr(result.claim, "domain", ClaimDomain.GENERAL_FACTUAL),
            risk_score=result.risk_score,
            status=result.status,
            confidence=getattr(result, "confidence", 0.0),
            reasoning=getattr(result, "reasoning", None),
            suggestion=getattr(result, "suggestion", None),
            suggested_sources=result.claim.suggested_sources,
            note=" | ".join(note_parts),
            citations=citations[:5],
            verification_details=verification_details,
        ))
    return claims_response


def _build_claims_response_from_db(analysis_results) -> list[ClaimResultResponse]:
    """Convert DB AnalysisResult models with their claims/evidence into API responses."""
    claims_response = []
    
    for analysis in analysis_results:
        summary_warnings = analysis.warnings or []
        for claim in analysis.claims:
            # Map VERDICT to proper Status Enum for UI colors
            status = _map_claim_status_from_verdict(claim.verdict)
                
            citations = []
            evidences = []
            max_ent = 0.0
            max_contra = 0.0
            max_neutral = 0.0
            sources_checked = set()
            
            for ev in claim.evidence:
                sources_checked.add(ev.source_type)
                if ev.source_url:
                    citations.append(ev.source_url)
                elif ev.source_title:
                    citations.append(ev.source_title)
                
                max_ent = max(max_ent, ev.nli_entailment_prob)
                max_contra = max(max_contra, ev.nli_contradiction_prob)
                max_neutral = max(max_neutral, ev.nli_neutral_prob)
                
                evidences.append({
                    "source_type": ev.source_type,
                    "source_tier": getattr(ev, "source_tier", None),
                    "source_url": ev.source_url,
                    "source_title": ev.source_title,
                    "document_name": None,
                    "chunk_index": None,
                    "message_index": None,
                    "snippet": ev.snippet[:300] if ev.snippet else "",
                    "nli_label": ev.nli_verdict,
                    "nli_scores": {
                        "entailment": ev.nli_entailment_prob,
                        "contradiction": ev.nli_contradiction_prob,
                        "neutral": ev.nli_neutral_prob
                    }
                })
            
            note_parts = [f"Status: {status.value}"]
            if getattr(claim, "reasoning", None):
                note_parts.append(claim.reasoning)
            elif status == ClaimStatus.UNVERIFIABLE_SOURCE:
                note_parts.append("Source link unreachable.")
            elif status == ClaimStatus.UNVERIFIED:
                note_parts.append("Could not find strong evidence.")
            elif max_contra > 0.3:
                note_parts.append(f"Contradiction detected (score: {max_contra:.2f})")
            elif max_ent > 0.7:
                note_parts.append(f"Strongly supported (score: {max_ent:.2f})")
                
            verification_details = {
                "entailment_score": max_ent,
                "contradiction_score": max_contra,
                "neutral_score": max_neutral,
                "sources_checked": list(sources_checked),
                "evidence": evidences
            }
            
            claims_response.append(ClaimResultResponse(
                id=claim.id,
                text=claim.claim_text,
                exact_quote=getattr(claim, "exact_quote", None),
                domain=_map_claim_domain(getattr(claim, "domain", claim.claim_type)),
                risk_score=claim.risk_score,
                status=status,
                confidence=getattr(claim, "confidence", 0.0) or 0.0,
                reasoning=getattr(claim, "reasoning", None),
                suggestion=getattr(claim, "suggestion", None),
                suggested_sources=[],
                note=" | ".join(note_parts),
                citations=citations[:5],
                verification_details=verification_details
            ))
            
    return claims_response


def _build_highlight_claims(verification_results) -> list[HighlightClaim]:
    """Convert verification results into highlight claims for the extension DOM."""
    highlight_claims = []
    for result in verification_results:
        # Build explanation note
        status_text = result.status.value if hasattr(result.status, 'value') else str(result.status)
        note_parts = [f"Status: {status_text}"]
        
        if hasattr(result, "reasoning") and result.reasoning:
            note_parts.append(result.reasoning)
        elif result.max_contradiction_score > 0.3:
            note_parts.append(f"Contradiction detected (score: {result.max_contradiction_score:.2f})")
        elif result.max_entailment_score > 0.7:
            note_parts.append(f"Supported by sources (score: {result.max_entailment_score:.2f})")

        # Collect citation URLs/names
        citations = []
        for ev in result.evidence:
            if ev.source_url:
                citations.append(ev.source_url)
            elif ev.source_title:
                citations.append(ev.source_title)
            elif ev.document_name:
                citations.append(ev.document_name)

        highlight_claims.append(HighlightClaim(
            text=result.claim.text,
            exact_quote=getattr(result.claim, "exact_quote", None),
            domain=getattr(result.claim, "domain", None),
            score=result.risk_score,
            note=" | ".join(note_parts),
            citations=citations[:5],  # Limit to 5 citations
        ))
    return highlight_claims


async def _persist_single_mode_analysis(
    request: DetectionRequest,
    pipeline_result: dict,
    db: AsyncSession,
) -> None:
    """Persist single-mode detection results.

    有 conversation_id 时尽量关联到站内 assistant 消息；没有（外部 Key 直调、
    文档页试调用）也落一条独立 AnalysisResult，保证仪表盘各时间范围统计不漏数。
    """
    from sqlalchemy import select
    from app.db.models import AnalysisResult, ClaimAnalysis, EvidenceItem, Message

    db_msg = None
    if request.conversation_id:
        message_query = select(Message).where(
            Message.conversation_id == request.conversation_id,
            Message.role == "assistant",
        )

        if request.assistant_message_id:
            message_query = message_query.where(Message.id == request.assistant_message_id)
        else:
            message_query = message_query.where(Message.analysis_result_id.is_(None))
            if request.model_response:
                message_query = message_query.where(Message.content == request.model_response)

        message_query = message_query.order_by(Message.created_at.desc()).limit(1)
        db_msg = (await db.execute(message_query)).scalar_one_or_none()

        if db_msg is not None and db_msg.analysis_result_id is not None:
            return  # 该消息已分析过，不重复落库
        if db_msg is None:
            logger.info(
                "Single-mode persistence: no target assistant message found for conversation %s, "
                "saving standalone analysis row",
                request.conversation_id,
            )

    warnings_payload = [
        warning.model_dump() if hasattr(warning, "model_dump") else warning
        for warning in pipeline_result["warnings"]
    ]

    ar = AnalysisResult(
        ai_response_text=request.model_response or "",
        overall_risk_score=pipeline_result["overall_risk"],
        risk_level=(
            pipeline_result["risk_level"].value
            if hasattr(pipeline_result["risk_level"], "value")
            else pipeline_result["risk_level"]
        ),
        model_id=request.model_id,
        warnings=warnings_payload,
    )
    db.add(ar)
    if db_msg is not None:
        db_msg.analysis_result = ar

    for claim_res in pipeline_result["verification_results"]:
        ca = ClaimAnalysis(
            analysis=ar,
            claim_text=claim_res.claim.text,
            claim_type=(
                claim_res.claim.domain.value
                if hasattr(claim_res.claim.domain, "value")
                else claim_res.claim.domain
            ),
            domain=(
                claim_res.claim.domain.value
                if hasattr(claim_res.claim.domain, "value")
                else claim_res.claim.domain
            ),
            exact_quote=claim_res.claim.exact_quote,
            importance_score=claim_res.claim.importance,
            risk_score=claim_res.risk_score,
            verdict=(
                claim_res.status.value
                if hasattr(claim_res.status, "value")
                else claim_res.status
            ),
            confidence=getattr(claim_res, "confidence", None),
            reasoning=getattr(claim_res, "reasoning", None),
            suggestion=getattr(claim_res, "suggestion", None),
        )
        db.add(ca)

        for ev in claim_res.evidence:
            db.add(
                EvidenceItem(
                    claim_analysis=ca,
                    source_type=ev.source_type.value if hasattr(ev.source_type, "value") else ev.source_type,
                    source_tier=ev.source_tier.value if hasattr(ev.source_tier, "value") else ev.source_tier,
                    source_url=ev.source_url,
                    source_title=ev.source_title,
                    snippet=ev.snippet,
                    nli_verdict=ev.nli_label.value if ev.nli_label else None,
                    nli_entailment_prob=ev.nli_scores.get("entailment", 0.0) if ev.nli_scores else 0.0,
                    nli_contradiction_prob=ev.nli_scores.get("contradiction", 0.0) if ev.nli_scores else 0.0,
                    nli_neutral_prob=ev.nli_scores.get("neutral", 0.0) if ev.nli_scores else 0.0,
                )
            )

    await db.commit()


# ── Single mode handler ──────────────────────────────────────────────────

async def _detect_single(request: DetectionRequest, db: AsyncSession) -> DetectionResponse:
    """Handle single-response detection (frontend mode)."""
    start_time = time.time()
    response_id = str(uuid.uuid4())

    logger.info(f"Detection request {response_id} [single]: model={request.model_id}, "
                f"response_length={len(request.model_response)}")

    pipeline_result = await _run_detection_pipeline(
        model_response=request.model_response,
        conversation_history=request.conversation_history,
        conversation_id=request.conversation_id,
        document_ids=request.document_ids,
        config={
            "check_web": request.config.check_web,
            "check_documents": request.config.check_documents,
            "check_conversation": request.config.check_conversation,
        },
    )

    try:
        await _persist_single_mode_analysis(request, pipeline_result, db)
    except Exception as persist_error:
        await db.rollback()
        logger.warning(
            "Single-mode analysis persistence failed but detection response will continue: %s",
            persist_error,
            exc_info=True,
        )

    claims_response = _build_claims_response(pipeline_result["verification_results"])
    processing_time = int((time.time() - start_time) * 1000)

    verification_results = pipeline_result["verification_results"]
    claims_verified = len([r for r in verification_results if r.status != ClaimStatus.SKIPPED])
    claims_skipped = len([r for r in verification_results if r.status == ClaimStatus.SKIPPED])

    all_sources = set()
    for r in verification_results:
        for s in r.sources_checked:
            all_sources.add(s.value)

    return DetectionResponse(
        response_id=response_id,
        overall_risk_score=pipeline_result["overall_risk"],
        risk_level=pipeline_result["risk_level"],
        risk_color=pipeline_result["risk_color"],
        warning_message=pipeline_result["warning_message"],
        warnings=pipeline_result["warnings"],
        claims=claims_response,
        metadata=DetectionMetadata(
            processing_time_ms=processing_time,
            claims_extracted=len(pipeline_result["extracted_claims"]),
            claims_verified=claims_verified,
            claims_skipped=claims_skipped,
            sources_queried=list(all_sources),
            conversation_id=request.conversation_id,
        ),
    )


# ── Extension mode handler ───────────────────────────────────────────────

async def _detect_extension(request: DetectionRequest, db: AsyncSession) -> DetectionResponse:
    """
    Handle extension-mode detection.
    
    1. Sync conversation to DB (find-or-create + dedup messages)
    2. For each NEW assistant message, run detection pipeline
    3. Return per-message results with highlighting data
    """
    start_time = time.time()
    response_id = str(uuid.uuid4())

    platform = request.platform
    ext_conv = request.conversation
    ext_messages = request.messages or []
    
    logger.info(f"Detection request {response_id} [extension/{platform}]: "
                f"conv={ext_conv.id if ext_conv else 'none'}, "
                f"messages={len(ext_messages)}")

    # ── Step 1: Sync conversation to DB ──────────────────────────────
    from app.api.conversations import find_or_create_conversation, sync_messages_to_conversation

    conv, was_created = await find_or_create_conversation(
        db=db,
        external_id=ext_conv.id if ext_conv else str(uuid.uuid4()),
        platform=platform,
        title=ext_conv.title if ext_conv else None,
        external_url=ext_conv.url if ext_conv else None,
    )

    new_count = await sync_messages_to_conversation(
        db=db,
        conversation=conv,
        messages=ext_messages,
        platform=platform,
    )
    await db.commit()

    logger.info(f"Synced conversation {conv.id}: {new_count} new messages")

    # ── Step 2: Identify assistant messages to analyze ────────────────
    # Analyze only assistant messages that still do not have an analysis result.
    # This keeps full-sync retries idempotent and avoids duplicate analyses.
    assistant_messages = [m for m in ext_messages if m.role == "assistant"]
    if assistant_messages:
        from app.db.models import Message
        from sqlalchemy import select

        assistant_external_ids = [m.id for m in assistant_messages if m.id]
        unprocessed_external_ids = set()

        if assistant_external_ids:
            pending_query = select(Message.external_id).where(
                Message.conversation_id == conv.id,
                Message.role == "assistant",
                Message.external_id.in_(assistant_external_ids),
                Message.analysis_result_id.is_(None),
            )
            pending_result = await db.execute(pending_query)
            unprocessed_external_ids = {row[0] for row in pending_result.fetchall()}

        filtered_assistant_messages = [
            m
            for m in assistant_messages
            if (m.id and m.id in unprocessed_external_ids) or not m.id
        ]

        skipped_existing = len(assistant_messages) - len(filtered_assistant_messages)
        assistant_messages = filtered_assistant_messages

        if skipped_existing > 0:
            logger.info(
                f"Skipping {skipped_existing} already-analyzed assistant messages for conversation {conv.id}"
            )
    
    if not assistant_messages:
        # No new assistant messages, but we will still fetch DB history in Step 4
        logger.info(f"No new assistant messages for {conv.id}. Fetching historical data.")

    # ── Step 3: Run detection for each assistant message ─────────────
    # 多条 assistant 消息时管线并行跑（原来 for 循环串行，3 条≈3 倍耗时）；
    # DB 写入仍串行（AsyncSession 不能并发用），先 gather 纯计算再逐条落库。
    all_claims_response = []
    all_warnings = []
    message_results = []
    seen_message_result_keys: set[str] = set()

    _detect_sema = asyncio.Semaphore(3)

    async def _run_one(msg):
        msg_index = msg.index if msg.index is not None else 0
        history_messages = [
            ConversationMessage(
                role=m.role,
                content=m.text,
                model_id=f"{platform}_extension" if m.role == "assistant" else None,
            )
            for m in ext_messages
            if m.index is not None and m.index < msg_index
        ]
        async with _detect_sema:
            return await _run_detection_pipeline(
                model_response=msg.text,
                conversation_history=history_messages,
                conversation_id=conv.id,
                document_ids=request.document_ids,
                config={
                    "check_web": request.config.check_web,
                    "check_documents": request.config.check_documents,
                    "check_conversation": request.config.check_conversation,
                },
                platform_sources=msg.sources,
            )

    pipeline_outcomes: list = await asyncio.gather(
        *[_run_one(m) for m in assistant_messages], return_exceptions=True
    )

    for assistant_msg, pipeline_result in zip(assistant_messages, pipeline_outcomes):
        if isinstance(pipeline_result, Exception):
            e = pipeline_result
            logger.error(f"Detection failed for message {assistant_msg.id}: {e}", exc_info=True)
            # Still add an empty result so highlighting doesn't break
            message_result = MessageDetectionResult(
                messageId=assistant_msg.id,
                messageIndex=assistant_msg.index,
                assistantRoleIndex=assistant_msg.roleIndex,
                role="assistant",
                risk_score=0.0,
                risk_level=RiskLevel.LOW,
                claims=[],
            )
            message_result_key = _build_message_result_key(
                message_result.messageId,
                message_result.messageIndex,
                message_result.assistantRoleIndex,
            )
            if message_result_key is None or message_result_key not in seen_message_result_keys:
                if message_result_key is not None:
                    seen_message_result_keys.add(message_result_key)
                message_results.append(message_result)
            continue

        try:

            # Build per-message claims and highlights
            claims = _build_claims_response(pipeline_result["verification_results"])
            highlights = _build_highlight_claims(pipeline_result["verification_results"])
            all_claims_response.extend(claims)
            all_warnings.extend(pipeline_result["warnings"])

            message_result = MessageDetectionResult(
                messageId=assistant_msg.id,
                messageIndex=assistant_msg.index,
                assistantRoleIndex=assistant_msg.roleIndex,
                role="assistant",
                risk_score=pipeline_result["overall_risk"],
                risk_level=pipeline_result["risk_level"],
                claims=highlights,
            )
            message_result_key = _build_message_result_key(
                message_result.messageId,
                message_result.messageIndex,
                message_result.assistantRoleIndex,
            )
            if message_result_key is None or message_result_key not in seen_message_result_keys:
                if message_result_key is not None:
                    seen_message_result_keys.add(message_result_key)
                message_results.append(message_result)

            # ── DB Persistence ────────────────────────────────────────────────
            from app.db.models import AnalysisResult, ClaimAnalysis, EvidenceItem, Message
            from sqlalchemy import select
            
            db_msg = (await db.execute(
                select(Message)
                .where(Message.conversation_id == conv.id, Message.external_id == assistant_msg.id)
            )).scalar_one_or_none()
            
            if db_msg and db_msg.analysis_result_id is None:
                # Create AnalysisResult
                ar = AnalysisResult(
                    ai_response_text=assistant_msg.text,
                    overall_risk_score=pipeline_result["overall_risk"],
                    risk_level=pipeline_result["risk_level"].value if hasattr(pipeline_result["risk_level"], 'value') else pipeline_result["risk_level"],
                    warnings=[w.model_dump() if hasattr(w, 'model_dump') else (w if isinstance(w, dict) else str(w)) for w in pipeline_result["warnings"]]
                )
                db.add(ar)
                db_msg.analysis_result = ar  # Link to Message
                
                # Save Claims
                for claim_res in pipeline_result["verification_results"]:
                    ca = ClaimAnalysis(
                        analysis=ar,
                        claim_text=claim_res.claim.text,
                        claim_type=claim_res.claim.domain.value if hasattr(claim_res.claim.domain, 'value') else claim_res.claim.domain,
                        domain=claim_res.claim.domain.value if hasattr(claim_res.claim.domain, 'value') else claim_res.claim.domain,
                        exact_quote=claim_res.claim.exact_quote,
                        importance_score=claim_res.claim.importance,
                        risk_score=claim_res.risk_score,
                        verdict=claim_res.status.value if hasattr(claim_res.status, 'value') else claim_res.status,
                        confidence=getattr(claim_res, "confidence", None),
                        reasoning=getattr(claim_res, "reasoning", None),
                        suggestion=getattr(claim_res, "suggestion", None),
                    )
                    db.add(ca)
                    
                    # Save Evidence
                    for ev in claim_res.evidence:
                        db.add(EvidenceItem(
                            claim_analysis=ca,
                            source_type=ev.source_type.value if hasattr(ev.source_type, 'value') else ev.source_type,
                            source_tier=ev.source_tier.value if hasattr(ev.source_tier, 'value') else ev.source_tier,
                            source_url=ev.source_url,
                            source_title=ev.source_title,
                            snippet=ev.snippet,
                            nli_verdict=ev.nli_label.value if ev.nli_label else None,
                            nli_entailment_prob=ev.nli_scores.get('entailment', 0.0) if ev.nli_scores else 0.0,
                            nli_contradiction_prob=ev.nli_scores.get('contradiction', 0.0) if ev.nli_scores else 0.0,
                            nli_neutral_prob=ev.nli_scores.get('neutral', 0.0) if ev.nli_scores else 0.0
                        ))
                        
                await db.commit()
            elif db_msg and db_msg.analysis_result_id is not None:
                logger.info(
                    f"Message {assistant_msg.id} already has analysis {db_msg.analysis_result_id}; skipping persistence."
                )



        except Exception as e:
            logger.error(f"Detection failed for message {assistant_msg.id}: {e}", exc_info=True)
            # Still add an empty result so highlighting doesn't break
            message_result = MessageDetectionResult(
                messageId=assistant_msg.id,
                messageIndex=assistant_msg.index,
                assistantRoleIndex=assistant_msg.roleIndex,
                role="assistant",
                risk_score=0.0,
                risk_level=RiskLevel.LOW,
                claims=[],
            )
            message_result_key = _build_message_result_key(
                message_result.messageId,
                message_result.messageIndex,
                message_result.assistantRoleIndex,
            )
            if message_result_key is None or message_result_key not in seen_message_result_keys:
                if message_result_key is not None:
                    seen_message_result_keys.add(message_result_key)
                message_results.append(message_result)

    # ── Step 4: Add PAST claims from DB ──────────────────────────────
    from app.db.models import AnalysisResult, Message, ClaimAnalysis
    from sqlalchemy.orm import selectinload
    from sqlalchemy import select
    from app.models.detect import Warning as WarningSchema, HighlightClaim
    
    # Query all historical AnalysisResults for this conversation WITH their Message
    query = (
        select(AnalysisResult, Message)
        .join(Message, Message.analysis_result_id == AnalysisResult.id)
        .where(Message.conversation_id == conv.id)
        .options(
            selectinload(AnalysisResult.claims).selectinload(ClaimAnalysis.evidence)
        )
    )
    db_result = await db.execute(query)
    
    past_analyses = []
    
    # We must rebuild MessageDetectionResult for past messages so the frontend can map them via external_id
    for past_analysis, msg in db_result.all():
        past_analyses.append(past_analysis)
        
        db_highlight_claims = []
        for claim in past_analysis.claims:
            status = _map_claim_status_from_verdict(claim.verdict)
                
            citations = []
            max_ent = 0.0
            max_contra = 0.0
            
            for ev in claim.evidence:
                if ev.source_url: citations.append(ev.source_url)
                elif ev.source_title: citations.append(ev.source_title)
                max_ent = max(max_ent, ev.nli_entailment_prob)
                max_contra = max(max_contra, ev.nli_contradiction_prob)

            note_parts = [f"Status: {status.value}"]
            if getattr(claim, "reasoning", None):
                note_parts.append(claim.reasoning)
            elif status == ClaimStatus.UNVERIFIABLE_SOURCE:
                note_parts.append("Source link unreachable.")
            elif status == ClaimStatus.UNVERIFIED:
                note_parts.append("Could not find strong evidence supporting or contradicting this claim.")
            elif status == ClaimStatus.PARTIALLY_VERIFIED:
                note_parts.append(f"Partially supported (entailment: {max_ent:.2f}).")
            elif status == ClaimStatus.OPINION:
                note_parts.append("Opinion/subjective claim.")
            elif max_contra > 0.3:
                note_parts.append(f"Contradiction detected (score: {max_contra:.2f})")
            elif max_ent > 0.7:
                note_parts.append(f"Strongly supported (score: {max_ent:.2f})")
                
            db_highlight_claims.append(HighlightClaim(
                text=claim.claim_text,
                exact_quote=getattr(claim, "exact_quote", None),
                domain=_map_claim_domain(getattr(claim, "domain", claim.claim_type)),
                score=claim.risk_score,
                note=" | ".join(note_parts),
                citations=citations[:5]
            ))

        scorer_instance = get_risk_scorer()
        message_result = MessageDetectionResult(
            messageId=msg.external_id,
            messageIndex=msg.message_index,
            assistantRoleIndex=msg.role_index,
            role="assistant",
            risk_score=past_analysis.overall_risk_score,
            risk_level=scorer_instance.get_risk_level(past_analysis.overall_risk_score),
            claims=db_highlight_claims
        )
        message_result_key = _build_message_result_key(
            message_result.messageId,
            message_result.messageIndex,
            message_result.assistantRoleIndex,
        )
        if message_result_key is None or message_result_key not in seen_message_result_keys:
            if message_result_key is not None:
                seen_message_result_keys.add(message_result_key)
            message_results.append(message_result)
    
    db_claims_response = _build_claims_response_from_db(past_analyses)
    
    # Merge them into the final response array without duplicating!
    existing_claim_keys = {
        _build_claim_response_dedupe_key(c) for c in all_claims_response
    }
    for c in db_claims_response:
        dedupe_key = _build_claim_response_dedupe_key(c)
        if dedupe_key in existing_claim_keys:
            continue
        existing_claim_keys.add(dedupe_key)
        all_claims_response.append(c)

    # Recompute overall_risk from deduped per-message results.
    all_scores = [float(result.risk_score) for result in message_results]
    overall_risk = sum(all_scores) / len(all_scores) if all_scores else 0.0
    
    scorer = get_risk_scorer()
    risk_level = scorer.get_risk_level(overall_risk)
    risk_color = scorer.get_risk_color(risk_level)
    warning_message = scorer.get_warning_message(risk_level)
    
    # Also collect past warnings
    for a in past_analyses:
        if a.warnings:
            for w in a.warnings:
                if isinstance(w, str):
                    continue
                elif isinstance(w, dict):
                    all_warnings.append(WarningSchema(**w))

    processing_time = int((time.time() - start_time) * 1000)
    claims_verified = len([c for c in all_claims_response if c.status != ClaimStatus.SKIPPED])
    claims_skipped = len([c for c in all_claims_response if c.status == ClaimStatus.SKIPPED])

    logger.info(
        f"Detection {response_id} [extension/{platform}] complete: "
        f"risk={overall_risk:.1f} ({risk_level.value}), "
        f"new_analyzed={len(assistant_messages)}, "
        f"total_claims={len(all_claims_response)}, "
        f"time={processing_time}ms"
    )

    return DetectionResponse(
        response_id=response_id,
        overall_risk_score=overall_risk,
        risk_level=risk_level,
        risk_color=risk_color,
        warning_message=warning_message,
        warnings=all_warnings,
        claims=all_claims_response,
        results=message_results,
        metadata=DetectionMetadata(
            processing_time_ms=processing_time,
            claims_extracted=len(all_claims_response),
            claims_verified=claims_verified,
            claims_skipped=claims_skipped,
            sources_queried=[],
            platform=platform,
            conversation_id=conv.id,
        ),
    )


# ── Unified endpoint ─────────────────────────────────────────────────────

@router.post("/detect", response_model=DetectionResponse)
async def detect_hallucinations(
    request: DetectionRequest,
    db: AsyncSession = Depends(get_db_session),
    _key_info: Optional[dict] = Depends(require_api_key_if_configured),
):
    """
    Detect hallucinations in AI-generated responses.
    
    Auto-detects payload format:
    
    **Single mode (frontend):**
    - Provide `model_response` + `conversation_history`
    - Returns analysis in `claims[]`
    
    **Extension mode (browser extensions):**
    - Provide `platform` + `conversation` + `messages[]`
    - Returns per-message analysis in `results[]` for DOM highlighting
    - Automatically syncs conversation to database
    
    Pipeline (same for both modes):
    1. Claim extraction (LLM)
    2. NER extraction (spaCy)
    3. Multi-source verification (web, docs, conversation)
    4. NLI classification (DeBERTa on GPU)
    5. Risk score aggregation
    """
    try:
        if request.is_extension_mode:
            return await _detect_extension(request, db)
        elif request.model_response:
            return await _detect_single(request, db)
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid request. Provide either:\n"
                    "- model_response + conversation_history (frontend mode)\n"
                    "- platform + conversation + messages[] (extension mode)"
                ),
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Detection pipeline failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Hallucination detection failed: {str(e)}"
        )
