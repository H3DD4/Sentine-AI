"""
Findings router.

Changes:
- selectinload(Finding.evidence) on delete (fixes MissingGreenlet with async SQLAlchemy)
- Pagination (skip / limit) on list endpoint
- eager-loads evidence in get_finding
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload
from app.db import get_session
from app.models import Finding, Evidence
from app.schemas import FindingOut, FindingCreate, FindingUpdate
import os

router = APIRouter(prefix="/findings", tags=["findings"])


@router.get("", response_model=list[FindingOut])
async def list_findings(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: str = Query(""),
    session: AsyncSession = Depends(get_session),
):
    query = select(Finding)
    if search.strip():
        term = f"%{search.strip()}%"
        query = query.where(or_(Finding.title.ilike(term), Finding.description.ilike(term)))
    result = await session.execute(
        query
        .options(selectinload(Finding.evidence))
        .order_by(Finding.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/{finding_id}", response_model=FindingOut)
async def get_finding(
    finding_id: str,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Finding)
        .options(selectinload(Finding.evidence))
        .where(Finding.id == finding_id)
    )
    finding = result.scalar_one_or_none()
    if not finding:
        raise HTTPException(404, "Finding not found")
    return finding


@router.post("", response_model=FindingOut, status_code=201)
async def create_finding(
    data: FindingCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a new finding manually (without running validation)."""
    finding = Finding(**data.model_dump())
    session.add(finding)
    await session.commit()
    result = await session.execute(
        select(Finding)
        .options(selectinload(Finding.evidence))
        .where(Finding.id == finding.id)
    )
    return result.scalar_one()


@router.patch("/{finding_id}", response_model=FindingOut)
async def update_finding(
    finding_id: str,
    update: FindingUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update analyst-owned finding fields."""
    result = await session.execute(
        select(Finding)
        .options(selectinload(Finding.evidence))
        .where(Finding.id == finding_id)
    )
    finding = result.scalar_one_or_none()
    if not finding:
        raise HTTPException(404, "Finding not found")
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(finding, field, value)
    await session.commit()
    result = await session.execute(
        select(Finding)
        .options(selectinload(Finding.evidence))
        .where(Finding.id == finding_id)
    )
    return result.scalar_one()


@router.delete("/{finding_id}", status_code=204)
async def delete_finding(
    finding_id: str,
    session: AsyncSession = Depends(get_session),
):
    # selectinload ensures evidence is loaded eagerly (avoids MissingGreenlet)
    result = await session.execute(
        select(Finding)
        .options(selectinload(Finding.evidence))
        .where(Finding.id == finding_id)
    )
    finding = result.scalar_one_or_none()
    if not finding:
        raise HTTPException(404, "Finding not found")

    for ev in finding.evidence:
        if ev.storage_path and os.path.exists(ev.storage_path):
            os.remove(ev.storage_path)
        await session.delete(ev)

    await session.delete(finding)
    await session.commit()
