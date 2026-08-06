from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import get_session
from app.config import settings
from app.models import AuditLog, Finding, GeneratedReport
from app.schemas import ChatRequest, GeneratedReportOut, ReportRequest
from app.services.report import generate_report_docx
from app.services.report_readiness import assess_conversation, draft_to_finding
import hashlib
import os

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("", response_model=list[GeneratedReportOut])
async def list_reports(
    skip: int = 0,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(GeneratedReport)
        .order_by(GeneratedReport.created_at.desc())
        .offset(max(skip, 0))
        .limit(min(max(limit, 1), 200))
    )
    return result.scalars().all()


@router.get("/{report_id}/download")
async def download_report(
    report_id: str,
    session: AsyncSession = Depends(get_session),
):
    report = await session.get(GeneratedReport, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    if not os.path.isfile(report.storage_path):
        raise HTTPException(410, "The stored report file is no longer available")
    return FileResponse(
        report.storage_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=report.filename,
    )


@router.delete("/{report_id}", status_code=204)
async def delete_report(
    report_id: str,
    session: AsyncSession = Depends(get_session),
):
    report = await session.get(GeneratedReport, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    if os.path.isfile(report.storage_path):
        os.remove(report.storage_path)
    await session.delete(report)
    await session.commit()


@router.post("/readiness")
async def assess_report_readiness(req: ChatRequest):
    if not req.messages:
        raise HTTPException(400, "At least one conversation message is required")
    try:
        return await assess_conversation(req.messages)
    except Exception as exc:
        raise HTTPException(502, f"Could not assess report readiness: {exc}") from exc


@router.post("/generate")
async def generate_report(
    req: ReportRequest,
    session: AsyncSession = Depends(get_session),
):
    findings = []
    if req.finding_ids:
        result = await session.execute(
            select(Finding).where(Finding.id.in_(req.finding_ids))
        )
        loaded = list(result.scalars().all())
        by_id = {finding.id: finding for finding in loaded}
        findings = [by_id[finding_id] for finding_id in req.finding_ids if finding_id in by_id]
        found_ids = {finding.id for finding in findings}
        missing_ids = [finding_id for finding_id in req.finding_ids if finding_id not in found_ids]
        if missing_ids:
            raise HTTPException(404, f"Findings not found: {', '.join(missing_ids)}")

    if req.draft:
        findings.insert(0, draft_to_finding(req.draft))

    if not findings:
        raise HTTPException(400, "Select at least one finding or provide an eligible conversation draft")

    docx_bytes = await generate_report_docx(findings, req.engagement_title, req.client_name)
    report_id = __import__("uuid").uuid4().hex
    filename = f"report-{report_id[:8]}.docx"
    os.makedirs(settings.REPORT_DIR, exist_ok=True)
    storage_path = os.path.join(settings.REPORT_DIR, f"{report_id}.docx")
    with open(storage_path, "wb") as report_file:
        report_file.write(docx_bytes)

    snapshot = [
        {
            "id": finding.id,
            "title": finding.title,
            "verdict": getattr(finding.verdict, "value", finding.verdict),
        }
        for finding in findings
        if getattr(finding, "id", None) in req.finding_ids
    ]
    report = GeneratedReport(
        id=report_id,
        client_name=req.client_name,
        engagement_title=req.engagement_title,
        filename=filename,
        storage_path=storage_path,
        finding_snapshot=snapshot,
        draft_snapshot=req.draft.model_dump(mode="json") if req.draft else None,
    )
    session.add(report)
    session.add(AuditLog(
        event_type="report_generated",
        input_hash=hashlib.sha256(docx_bytes).hexdigest(),
        payload_summary={"finding_ids": req.finding_ids, "includes_draft": req.draft is not None},
        result_summary={"report_id": report_id, "filename": filename},
    ))
    await session.commit()
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "X-Report-ID": report_id,
        },
    )
