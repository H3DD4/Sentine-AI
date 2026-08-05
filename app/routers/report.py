from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import get_session
from app.models import Finding
from app.schemas import ReportRequest
from app.services.report import generate_report_docx

router = APIRouter(prefix="/reports", tags=["reports"])

@router.post("/generate")
async def generate_report(
    req: ReportRequest,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Finding).where(Finding.id.in_(req.finding_ids))
    )
    findings = result.scalars().all()
    docx_bytes = await generate_report_docx(findings, req.engagement_title, req.client_name)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=redteam_report.docx"},
    )
