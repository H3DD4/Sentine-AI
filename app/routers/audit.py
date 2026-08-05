from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from app.db import get_session
from app.models import AuditLog
from app.schemas import AuditLogOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogOut])
async def list_audit_logs(
    event_type: Optional[str] = Query(None, alias="event_type"),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(AuditLog).order_by(AuditLog.timestamp.desc())
    if event_type:
        stmt = stmt.where(AuditLog.event_type == event_type)
    result = await session.execute(stmt)
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "event_type": log.event_type,
            "finding_id": log.finding_id,
            "user_id": log.user_id,
            "input_hash": log.input_hash,
            "payload_summary": log.payload_summary or {},
            "result_summary": log.result_summary or {},
            "timestamp": log.timestamp.isoformat() if log.timestamp else "",
        }
        for log in logs
    ]