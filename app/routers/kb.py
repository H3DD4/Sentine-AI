"""
Knowledge Base router — multi-source.

Every endpoint is source-aware.  `/kb/sources` is the endpoint the UI uses to
show the analyst which corpora are live, how many documents each holds, and
which are degraded or unavailable — the "always be explicit about what data is
being used" requirement, served from the same health data the retrieval
orchestrator routes on, so the display can never disagree with the behaviour.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.kb.indexer import delete_document, index_rows, reindex_source
from app.kb.models import InternalDoc
from app.kb.registry import all_sources, get_health, get_source, source_keys
from app.schemas import KBEntryCreate
from app.services.retrieval import federated_search, get_qdrant

router = APIRouter(prefix="/kb", tags=["knowledge-base"])


# ── Source visibility ────────────────────────────────────────────────────────


@router.get("/sources")
async def list_sources(
    refresh: bool = Query(False, description="Bypass the 30s health cache"),
    session: AsyncSession = Depends(get_session),
):
    """
    Per-source health: what is indexed, what is stale, what is down.

    This is the contract behind the UI's source badges. `status` is one of
    ok | no_match | empty | degraded | unavailable | disabled, and `detail`
    is a sentence safe to show the analyst verbatim.
    """
    health = await get_health(session, get_qdrant(), force=refresh)
    sources = [health[k].to_dict() for k in source_keys() if k in health]
    usable = [s for s in sources if s["status"] in ("ok", "degraded")]
    return {
        "sources": sources,
        "usable_count": len(usable),
        "total_count": len(sources),
        # If nothing is usable the agent must say so rather than answer from
        # the model's own memory and present it as grounded.
        "retrieval_available": bool(usable),
    }


@router.post("/sources/{source_key}/reindex")
async def reindex(
    source_key: str,
    force: bool = Query(False, description="Re-embed even if content is unchanged"),
    session: AsyncSession = Depends(get_session),
):
    """Rebuild a source's vectors from its Postgres rows."""
    try:
        source = get_source(source_key)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None

    stats = await reindex_source(source, session, get_qdrant(), force=force)
    return {"source": source_key, **stats.to_dict()}


# ── Search ───────────────────────────────────────────────────────────────────


@router.get("/search")
async def search_kb(
    q: str = Query(..., min_length=1),
    cvss_min: Optional[float] = Query(None),
    sources: Optional[str] = Query(
        None, description="Comma-separated source keys; omit to search all"
    ),
    top_k: int = Query(8, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    """
    Federated search across every knowledge source.

    Returns the hits *and* the provenance record. Clients must render
    `sources` / `provenance` alongside results — a result list without its
    source report hides the fact that a corpus was unavailable.
    """
    source_list = [s.strip() for s in sources.split(",")] if sources else None
    outcome = await federated_search(
        q,
        session,
        sources=source_list,
        cvss_min=cvss_min or 0.0,
        top_k=top_k,
    )
    return outcome.to_dict()


# ── Per-source document listing ──────────────────────────────────────────────


@router.get("/entries")
async def list_entries(
    source: str = Query("nvd", description=f"One of: {', '.join(source_keys())}"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    try:
        src = get_source(source)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None

    order_col = _recency_column(src)
    stmt = select(src.model).offset(skip).limit(limit)
    if order_col is not None:
        stmt = stmt.order_by(order_col.desc().nullslast())
    else:
        stmt = stmt.order_by(src.pk_column)

    rows = (await session.execute(stmt)).scalars().all()
    return {
        "source": source,
        "label": src.display_name,
        "entries": [_serialize(src, r) for r in rows],
    }


@router.get("/entries/count")
async def count_entries(
    source: Optional[str] = Query(None, description="Omit for per-source totals"),
    session: AsyncSession = Depends(get_session),
):
    if source:
        try:
            src = get_source(source)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from None
        return {"source": source, "count": await src.row_count(session)}

    counts = {}
    total = 0
    for src in all_sources():
        try:
            n = await src.row_count(session)
        except Exception:
            await session.rollback()
            n = 0
        counts[src.key] = n
        total += n
    return {"counts": counts, "total": total}


# ── Manual internal-KB authoring ─────────────────────────────────────────────


@router.post("/entries", status_code=201)
async def add_internal_entry(
    entry: KBEntryCreate,
    session: AsyncSession = Depends(get_session),
):
    """
    Add an analyst-authored document.

    Manual entries always land in the `internal` source. NVD and MITRE are
    machine-synced mirrors of upstream feeds — hand-writing into them would be
    silently overwritten on the next sync.
    """
    src = get_source("internal")
    data = entry.model_dump()
    new_id = str(uuid.uuid4())

    row = InternalDoc(
        id=new_id,
        title=data.get("title", ""),
        body=data.get("description", ""),
        doc_type=data.get("entry_type") or "note",
        mitre_techniques=data.get("mitre_techniques") or [],
        validation_steps=data.get("validation_steps") or [],
        indicators=data.get("indicators") or [],
        ref_urls=data.get("references") or [],
        tags=data.get("affected_products") or [],
    )
    session.add(row)
    await session.commit()

    stats = await index_rows(src, session, get_qdrant(), [row])
    return {"id": new_id, "source": "internal", "indexed": stats.indexed}


@router.delete("/entries/{source}/{doc_id}", status_code=204)
async def delete_entry(
    source: str,
    doc_id: str,
    session: AsyncSession = Depends(get_session),
):
    try:
        src = get_source(source)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None

    row = await session.get(src.model, doc_id)
    if row is None:
        raise HTTPException(404, f"{source} document '{doc_id}' not found")

    # Vectors first: an orphaned vector would keep being retrieved and cited
    # after its row is gone, whereas an orphaned row is merely un-searchable.
    await delete_document(get_qdrant(), src, doc_id)
    await session.delete(row)
    await session.commit()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _recency_column(src):
    for name in ("last_modified", "gw_updated_at", "updated_at"):
        col = getattr(src.model, name, None)
        if col is not None:
            return col
    return None


def _serialize(src, row) -> dict:
    """Render a row using the source's own payload builder, so the list view
    and the retrieval citations always agree on field names."""
    payload = src.build_payload(row)
    desc = payload.get("description") or ""
    payload["description"] = desc[:500]
    payload["synced"] = getattr(row, "qdrant_synced_at", None) is not None
    return payload
