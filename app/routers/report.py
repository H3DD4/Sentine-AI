from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import get_session
from app.models import Finding
from app.schemas import ChatRequest, ReportRequest
from app.services.report import generate_report_docx
from app.services.report_readiness import assess_conversation, draft_to_finding

router = APIRouter(prefix="/reports", tags=["reports"])


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
        findings = list(result.scalars().all())
        found_ids = {finding.id for finding in findings}
        missing_ids = [finding_id for finding_id in req.finding_ids if finding_id not in found_ids]
        if missing_ids:
            raise HTTPException(404, f"Findings not found: {', '.join(missing_ids)}")

    if req.draft:
        findings.insert(0, draft_to_finding(req.draft))

    if not findings:
        raise HTTPException(400, "Select at least one finding or provide an eligible conversation draft")

    docx_bytes = await generate_report_docx(findings, req.engagement_title, req.client_name)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=redteam_report.docx"},
    )
