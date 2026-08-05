from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import get_session
from app.models import Engagement
from app.schemas import EngagementCreate, EngagementUpdate, EngagementOut
from datetime import datetime

router = APIRouter(prefix="/engagements", tags=["engagements"])

@router.get("", response_model=list[EngagementOut])
async def list_engagements(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Engagement).order_by(Engagement.created_at.desc())
    )
    engagements = result.scalars().all()
    return [
        {
            **{k: getattr(e, k) for k in [
                "id", "client_name", "code", "scope", "progress",
                "findings_count", "lead", "status"
            ]},
            "start_date": e.start_date.isoformat() if e.start_date else None,
            "end_date": e.end_date.isoformat() if e.end_date else None,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in engagements
    ]

@router.get("/{engagement_id}", response_model=EngagementOut)
async def get_engagement(
    engagement_id: str,
    session: AsyncSession = Depends(get_session),
):
    engagement = await session.get(Engagement, engagement_id)
    if not engagement:
        raise HTTPException(404, "Engagement not found")
    return {
        **{k: getattr(engagement, k) for k in [
            "id", "client_name", "code", "scope", "progress",
            "findings_count", "lead", "status"
        ]},
        "start_date": engagement.start_date.isoformat() if engagement.start_date else None,
        "end_date": engagement.end_date.isoformat() if engagement.end_date else None,
        "created_at": engagement.created_at.isoformat() if engagement.created_at else None,
    }

@router.post("", response_model=EngagementOut, status_code=201)
async def create_engagement(
    data: EngagementCreate,
    session: AsyncSession = Depends(get_session),
):
    engagement = Engagement(
        client_name=data.client_name,
        code=data.code,
        scope=data.scope,
        lead=data.lead,
        status=data.status,
        start_date=datetime.fromisoformat(data.start_date) if data.start_date else None,
        end_date=datetime.fromisoformat(data.end_date) if data.end_date else None,
    )
    session.add(engagement)
    await session.commit()
    await session.refresh(engagement)
    return {
        **{k: getattr(engagement, k) for k in [
            "id", "client_name", "code", "scope", "progress",
            "findings_count", "lead", "status"
        ]},
        "start_date": engagement.start_date.isoformat() if engagement.start_date else None,
        "end_date": engagement.end_date.isoformat() if engagement.end_date else None,
        "created_at": engagement.created_at.isoformat() if engagement.created_at else None,
    }

@router.patch("/{engagement_id}", response_model=EngagementOut)
async def update_engagement(
    engagement_id: str,
    data: EngagementUpdate,
    session: AsyncSession = Depends(get_session),
):
    engagement = await session.get(Engagement, engagement_id)
    if not engagement:
        raise HTTPException(404, "Engagement not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        if key in ("start_date", "end_date") and value:
            setattr(engagement, key, datetime.fromisoformat(value))
        elif value is not None:
            setattr(engagement, key, value)
    await session.commit()
    await session.refresh(engagement)
    return {
        **{k: getattr(engagement, k) for k in [
            "id", "client_name", "code", "scope", "progress",
            "findings_count", "lead", "status"
        ]},
        "start_date": engagement.start_date.isoformat() if engagement.start_date else None,
        "end_date": engagement.end_date.isoformat() if engagement.end_date else None,
        "created_at": engagement.created_at.isoformat() if engagement.created_at else None,
    }

@router.delete("/{engagement_id}", status_code=204)
async def delete_engagement(
    engagement_id: str,
    session: AsyncSession = Depends(get_session),
):
    engagement = await session.get(Engagement, engagement_id)
    if not engagement:
        raise HTTPException(404, "Engagement not found")
    await session.delete(engagement)
    await session.commit()
