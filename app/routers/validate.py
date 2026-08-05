"""
Validation router.

Changes:
- Filename sanitized (Path(filename).name prevents path traversal)
- File size limit (50 MB total per request)
- Uses safe_filename from parse_evidence()
- Paginated findings already handled elsewhere
"""

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.schemas import ValidationResponse, ValidationResult, FindingCreate
from app.services.validation import validate_finding
from app.services.evidence import parse_evidence
from app.models import Finding, Evidence, AuditLog
import hashlib, json, os
from app.config import settings

router = APIRouter(prefix="/validate", tags=["validation"])

MAX_FILES = 10
MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MB


@router.post("", response_model=ValidationResponse)
async def validate_endpoint(
    title: str = Form(...),
    description: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    session: AsyncSession = Depends(get_session),
):
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Too many files (max {MAX_FILES})")

    # Parse uploaded evidence
    evidence_texts, image_descriptions, parsed_evidence = [], [], []
    total_bytes = 0

    for upload in files:
        file_bytes = await upload.read()
        total_bytes += len(file_bytes)
        if total_bytes > MAX_TOTAL_BYTES:
            raise HTTPException(413, "Total upload size exceeds 50 MB limit")

        filename = upload.filename or "unknown"
        parsed = await parse_evidence(filename, file_bytes)
        parsed["file_bytes"] = file_bytes
        parsed_evidence.append(parsed)

        if parsed["extracted_text"]:
            evidence_texts.append(parsed["extracted_text"])
        if parsed["image_description"]:
            image_descriptions.append(parsed["image_description"])

    # Run AI validation. The search outcome comes back alongside the verdict
    # so the response can state which corpora backed it.
    result, outcome = await validate_finding(
        description, evidence_texts, image_descriptions, session
    )

    # Persist finding
    finding = Finding(
        title=title,
        description=description,
        verdict=result.verdict,
        confidence=result.confidence,
        reasoning=result.reasoning,
        matched_cves=result.matched_cves,
        matched_techniques=result.matched_techniques,
        missing_evidence=result.missing_evidence,
        recommended_next_steps=result.recommended_next_steps,
    )
    session.add(finding)
    await session.flush()

    # Persist evidence records using sanitized filenames
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    for p in parsed_evidence:
        safe_name = p.get("safe_filename", "evidence")
        path = os.path.join(settings.UPLOAD_DIR, f"{finding.id}_{safe_name}")
        with open(path, "wb") as f:
            f.write(p["file_bytes"])
        ev = Evidence(
            finding_id=finding.id,
            filename=safe_name,
            file_type=p["file_type"],
            storage_path=path,
            extracted_text=p.get("extracted_text"),
            image_description=p.get("image_description"),
        )
        session.add(ev)

    # Audit log. The record keeps which sources were reachable at the time,
    # because re-running the same validation next week can legitimately give a
    # different verdict once a source that was down has come back.
    input_hash = hashlib.sha256((title + description).encode()).hexdigest()[:16]
    session.add(AuditLog(
        event_type="validation",
        finding_id=finding.id,
        input_hash=input_hash,
        payload_summary={"title": title, "evidence_count": len(files)},
        result_summary={
            "verdict": result.verdict,
            "confidence": result.confidence,
            "sources_used": outcome.sources_used,
            "degraded": outcome.degraded,
            "provenance": outcome.provenance_line(),
        },
    ))
    await session.commit()

    data = outcome.to_dict()
    return ValidationResponse(
        **result.model_dump(),
        finding_id=finding.id,
        sources=data["sources"],
        sources_used=data["sources_used"],
        provenance=data["provenance"],
        degraded=data["degraded"],
        citations=data["results"],
    )
