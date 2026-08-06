from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import get_session
from app.config import settings
from app.models import AuditLog, Finding, GeneratedReport, ReportTemplate
from app.schemas import ChatRequest, GeneratedReportOut, ReportRequest, ReportTemplateOut
from app.services.report import generate_report_docx
from app.services.report_readiness import assess_conversation, draft_to_finding
import hashlib
import os
import uuid
from pathlib import Path
from docx import Document

router = APIRouter(prefix="/reports", tags=["reports"])

TEMPLATE_MAX_BYTES = 10 * 1024 * 1024


@router.get("/templates", response_model=list[ReportTemplateOut])
async def list_templates(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(ReportTemplate).order_by(ReportTemplate.is_active.desc(), ReportTemplate.created_at.desc())
    )
    return result.scalars().all()


@router.post("/templates", response_model=ReportTemplateOut, status_code=201)
async def upload_template(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    name = Path(file.filename or "template.docx").name
    if Path(name).suffix.lower() != ".docx":
        raise HTTPException(400, "Report templates must be DOCX files")
    os.makedirs(settings.REPORT_TEMPLATE_DIR, exist_ok=True)
    template_id = uuid.uuid4().hex
    path = Path(settings.REPORT_TEMPLATE_DIR) / f"{template_id}.docx"
    size = 0
    try:
        with path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > TEMPLATE_MAX_BYTES:
                    raise HTTPException(413, "Template exceeds the 10 MB limit")
                destination.write(chunk)
        Document(str(path))
    except Exception as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(400, f"Invalid DOCX template: {exc}") from exc

    existing = await session.execute(select(ReportTemplate))
    templates = list(existing.scalars().all())
    active = not templates
    template = ReportTemplate(
        id=template_id,
        name=name,
        storage_path=str(path),
        size_bytes=size,
        is_active=active,
    )
    session.add(template)
    await session.commit()
    await session.refresh(template)
    return template


@router.post("/templates/{template_id}/activate", response_model=ReportTemplateOut)
async def activate_template(template_id: str, session: AsyncSession = Depends(get_session)):
    template = await session.get(ReportTemplate, template_id)
    if not template:
        raise HTTPException(404, "Template not found")
    result = await session.execute(select(ReportTemplate))
    for item in result.scalars().all():
        item.is_active = item.id == template_id
    await session.commit()
    await session.refresh(template)
    return template


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(template_id: str, session: AsyncSession = Depends(get_session)):
    template = await session.get(ReportTemplate, template_id)
    if not template:
        raise HTTPException(404, "Template not found")
    Path(template.storage_path).unlink(missing_ok=True)
    await session.delete(template)
    await session.commit()


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

    incomplete = []
    for finding in findings:
        missing = _missing_report_fields(finding)
        if missing:
            incomplete.append(f"{finding.title}: {', '.join(missing)}")
    if incomplete:
        raise HTTPException(
            422,
            "Complete the selected findings before export. " + " | ".join(incomplete),
        )

    template = None
    if req.template_id:
        template = await session.get(ReportTemplate, req.template_id)
        if not template:
            raise HTTPException(404, "Report template not found")
    else:
        result = await session.execute(
            select(ReportTemplate).where(ReportTemplate.is_active.is_(True)).limit(1)
        )
        template = result.scalar_one_or_none()
    if template and not os.path.isfile(template.storage_path):
        raise HTTPException(410, "The selected report template file is no longer available")

    docx_bytes = await generate_report_docx(
        findings,
        req.engagement_title,
        req.client_name,
        template_path=template.storage_path if template else None,
        sections=set(req.sections),
    )
    report_id = uuid.uuid4().hex
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
        payload_summary={
            "finding_ids": req.finding_ids,
            "includes_draft": req.draft is not None,
            "template_id": template.id if template else None,
            "sections": req.sections,
        },
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


def _missing_report_fields(finding: Finding) -> list[str]:
    required = {
        "title": finding.title,
        "description": finding.description,
        "affected scope": finding.affected_scope,
        "technical evidence": finding.technical_evidence,
        "impact": finding.impact,
        "severity": finding.severity,
        "analyst verdict": _verdict(finding),
    }
    return [label for label, value in required.items() if not value or not str(value).strip()]


def _verdict(finding: Finding) -> str:
    return getattr(finding.verdict, "value", finding.verdict) or ""
