"""
Validation router.

Changes:
- Filename sanitized (Path(filename).name prevents path traversal)
- File size limit (50 MB total per request)
- Uses safe_filename from parse_evidence()
- Paginated findings already handled elsewhere
"""

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.schemas import ImpactRefinementRequest, ValidationResponse
from app.services.validation import impact_narrative, validate_finding
from app.services.upload_processing import (
    cleanup_staged_evidence,
    move_staged_evidence,
    stage_evidence_uploads,
)
from app.models import Finding, Evidence, AuditLog
import hashlib
import json
import time

router = APIRouter(prefix="/validate", tags=["validation"])


def _legacy_cvss(result):
    cvss = result.impact_assessment.cvss
    if cvss.status != "exact":
        return "", None, ""
    return cvss.severity, cvss.score, cvss.vector


def _validation_response(result, outcome, finding_id: str, processing: dict | None = None):
    data = outcome.to_dict()
    return ValidationResponse(
        **result.model_dump(),
        finding_id=finding_id,
        sources=data["sources"],
        sources_used=data["sources_used"],
        provenance=data["provenance"],
        degraded=data["degraded"],
        citations=data["results"],
        processing=processing,
    )

@router.post("", response_model=ValidationResponse)
async def validate_endpoint(
    response: Response,
    title: str = Form(...),
    description: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    session: AsyncSession = Depends(get_session),
):
    request_started = time.perf_counter()
    parsed_evidence, processing = await stage_evidence_uploads(files)
    processing_finished = time.perf_counter()
    evidence_texts = [p["analysis_text"] for p in parsed_evidence if p.get("analysis_text")]
    image_descriptions = [
        p["image_description"] for p in parsed_evidence if p["image_description"]
    ]
    persisted_evidence = "\n\n".join(
        f"{item['filename']}:\n{content}"
        for item in parsed_evidence
        if (content := item.get("image_description") or item.get("extracted_text"))
    )[:32_000]

    # Run AI validation. The search outcome comes back alongside the verdict
    # so the response can state which corpora backed it.
    try:
        result, outcome = await validate_finding(
            description, evidence_texts, image_descriptions, session
        )
        validation_finished = time.perf_counter()
    except Exception:
        cleanup_staged_evidence(parsed_evidence)
        raise

    # Persist finding
    severity, cvss_score, cvss_vector = _legacy_cvss(result)
    finding = Finding(
        title=title,
        description=description,
        verdict=result.verdict,
        confidence=result.confidence,
        reasoning=result.reasoning,
        technical_evidence=persisted_evidence,
        impact=impact_narrative(result),
        impact_assessment=result.impact_assessment.model_dump(mode="json"),
        severity=severity,
        cvss_score=cvss_score,
        cvss_vector=cvss_vector,
        matched_cves=result.matched_cves,
        matched_techniques=result.matched_techniques,
        missing_evidence=result.missing_evidence,
        recommended_next_steps=result.recommended_next_steps,
    )
    session.add(finding)
    await session.flush()

    try:
        move_staged_evidence(parsed_evidence, finding.id)
        for p in parsed_evidence:
            session.add(Evidence(
                finding_id=finding.id,
                filename=p["filename"],
                file_type=p["file_type"],
                storage_path=p["storage_path"],
                extracted_text=p.get("extracted_text"),
                image_description=p.get("image_description"),
            ))

        # Keep the processing manifest with the audit event so later reviewers
        # can distinguish complete storage from bounded model context.
        input_hash = hashlib.sha256((title + description).encode()).hexdigest()[:16]
        session.add(AuditLog(
            event_type="validation",
            finding_id=finding.id,
            input_hash=input_hash,
            payload_summary={
                "title": title,
                "evidence_count": len(files),
                "processing": processing,
            },
            result_summary={
                "verdict": result.verdict,
                "confidence": result.confidence,
                "business_priority": result.impact_assessment.business_priority.value,
                "business_context_complete": result.impact_assessment.context_complete,
                "sources_used": outcome.sources_used,
                "degraded": outcome.degraded,
                "provenance": outcome.provenance_line(),
            },
        ))
        await session.commit()
        persistence_finished = time.perf_counter()
    except Exception:
        cleanup_staged_evidence(parsed_evidence)
        await session.rollback()
        raise

    response.headers["Server-Timing"] = (
        f"evidence;dur={(processing_finished - request_started) * 1000:.0f}, "
        f"validation;dur={(validation_finished - processing_finished) * 1000:.0f}, "
        f"persistence;dur={(persistence_finished - validation_finished) * 1000:.0f}"
    )
    return _validation_response(result, outcome, finding.id, processing)


@router.post("/{finding_id}/impact", response_model=ValidationResponse)
async def refine_impact_endpoint(
    finding_id: str,
    request: ImpactRefinementRequest,
    session: AsyncSession = Depends(get_session),
):
    finding = await session.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(404, "Finding not found")

    evidence = list((await session.scalars(
        select(Evidence).where(Evidence.finding_id == finding_id)
    )).all())
    evidence_texts = [item.extracted_text for item in evidence if item.extracted_text]
    image_descriptions = [item.image_description for item in evidence if item.image_description]
    prior_context = finding.impact_assessment or {}
    description = (
        f"{finding.description}\n\n"
        "=== PRIOR IMPACT ASSESSMENT ===\n"
        f"{json.dumps(prior_context, indent=2)}\n\n"
        "=== ANALYST-PROVIDED TARGET AND BUSINESS CONTEXT ===\n"
        f"{request.context}"
    )
    result, outcome = await validate_finding(
        description, evidence_texts, image_descriptions, session
    )

    finding.verdict = result.verdict
    finding.confidence = result.confidence
    finding.reasoning = result.reasoning
    finding.impact = impact_narrative(result)
    finding.impact_assessment = result.impact_assessment.model_dump(mode="json")
    severity, cvss_score, cvss_vector = _legacy_cvss(result)
    finding.severity = severity
    finding.cvss_score = cvss_score
    finding.cvss_vector = cvss_vector
    finding.matched_cves = result.matched_cves
    finding.matched_techniques = result.matched_techniques
    finding.missing_evidence = result.missing_evidence
    finding.recommended_next_steps = result.recommended_next_steps
    session.add(AuditLog(
        event_type="impact_refinement",
        finding_id=finding.id,
        input_hash=hashlib.sha256(request.context.encode()).hexdigest()[:16],
        payload_summary={"context_chars": len(request.context)},
        result_summary={
            "verdict": result.verdict.value,
            "confidence": result.confidence,
            "business_priority": result.impact_assessment.business_priority.value,
            "business_context_complete": result.impact_assessment.context_complete,
            "provenance": outcome.provenance_line(),
        },
    ))
    await session.commit()
    return _validation_response(result, outcome, finding.id)
