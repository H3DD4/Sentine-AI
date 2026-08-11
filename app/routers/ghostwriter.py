import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.schemas import GhostwriterPushRequest
from app.models import Finding, AuditLog
from app.services.ghostwriter_client import push_finding, get_projects

router = APIRouter(prefix="/ghostwriter", tags=["ghostwriter"])

@router.get("/projects")
async def list_projects():
    try:
        return await get_projects()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"Ghostwriter connection failed: {exc}") from exc

@router.post("/push")
async def push_to_ghostwriter(
    req: GhostwriterPushRequest,
    session: AsyncSession = Depends(get_session),
):
    finding = await session.get(Finding, req.finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found")
    if finding.verdict in ("false_positive", "insufficient") and not finding.analyst_confirmed:
        raise HTTPException(400, "Analyst must confirm this verdict before pushing to Ghostwriter")

    try:
        gw_id = await push_finding(finding, req.project_id)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"Ghostwriter push failed: {exc}") from exc
    finding.ghostwriter_finding_id = gw_id
    session.add(AuditLog(
        event_type="ghostwriter_push",
        finding_id=finding.id,
        input_hash=gw_id,
        payload_summary={"project_id": req.project_id},
        result_summary={"ghostwriter_finding_id": gw_id},
    ))
    await session.commit()
    return {"ghostwriter_finding_id": gw_id}
