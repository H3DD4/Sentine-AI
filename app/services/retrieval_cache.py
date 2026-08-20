"""Read-only, scope-aware retrieval caching and conversation workspace helpers."""

from __future__ import annotations

import copy
import hashlib
import json
import random
import threading
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.ingestion.embedder import INDEX_TEXT_FORMAT_VERSION, SPARSE_MODEL_NAME
from app.kb.base import Availability, RetrievalHit, SearchOutcome, SourceReport
from app.kb.models import KBSourceState


_cache: dict[str, tuple[float, SearchOutcome]] = {}
_lock = threading.RLock()
_redis = None


def _redis_enabled() -> bool:
    return settings.RETRIEVAL_CACHE_BACKEND.lower() in {"auto", "redis"}


async def _redis_client():
    global _redis
    if not _redis_enabled():
        return None
    if _redis is not None:
        return _redis
    try:
        import redis.asyncio as redis
        _redis = redis.from_url(
            settings.RETRIEVAL_CACHE_REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        await _redis.ping()
        return _redis
    except Exception:
        _redis = None
        return None


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def retrieval_cache_key(
    *,
    user_id: str,
    conversation_id: str,
    query: str,
    sources: list[str] | None = None,
    cvss_min: float = 0.0,
    payload_filter: dict | None = None,
    top_k: int = 10,
    rerank: bool = True,
    index_revision: str = "unknown",
) -> str:
    material = {
        "user": user_id,
        "conversation": conversation_id,
        "query": " ".join(query.split()).strip().lower(),
        "sources": sorted(sources or []),
        "cvss_min": cvss_min,
        "payload_filter": payload_filter or {},
        "top_k": top_k,
        "rerank": rerank,
        # Keep cache-key construction cold-start safe. The embedding model is
        # loaded by the retrieval startup path, never as a cache side effect.
        "embed_signature": (
            f"{settings.EMBEDDING_MODEL}|{SPARSE_MODEL_NAME}|"
            f"{max(32, int(settings.EMBEDDING_CHUNK_TOKENS))}|"
            f"{max(0, int(settings.EMBEDDING_CHUNK_OVERLAP_TOKENS))}|"
            f"{INDEX_TEXT_FORMAT_VERSION}"
        ),
        "index_revision": index_revision,
    }
    digest = hashlib.sha256(_stable(material).encode()).hexdigest()
    return f"retrieval:v1:{digest}"


async def current_index_revision(
    session: AsyncSession, sources: list[str] | None = None
) -> str:
    statement = select(
        KBSourceState.source_key,
        KBSourceState.row_count,
        KBSourceState.vector_count,
        KBSourceState.unsynced_count,
        KBSourceState.last_sync_completed_at,
    )
    if sources:
        statement = statement.where(KBSourceState.source_key.in_(sources))
    records = (await session.execute(statement)).all()
    material = sorted(
        (key, row_count, vectors, unsynced, completed.isoformat() if completed else "")
        for key, row_count, vectors, unsynced, completed in records
    )
    return hashlib.sha256(_stable(material).encode()).hexdigest()[:20]


def get_local(key: str) -> SearchOutcome | None:
    if not settings.RETRIEVAL_CACHE_ENABLED:
        return None
    now = time.monotonic()
    with _lock:
        item = _cache.get(key)
        if item is None:
            return None
        expires_at, outcome = item
        if expires_at <= now:
            _cache.pop(key, None)
            return None
        return copy.deepcopy(outcome)


def put_local(key: str, outcome: SearchOutcome) -> None:
    if not settings.RETRIEVAL_CACHE_ENABLED:
        return
    with _lock:
        _cache[key] = (
            time.monotonic() + _ttl_seconds(),
            copy.deepcopy(outcome),
        )
        while len(_cache) > max(1, settings.RETRIEVAL_CACHE_MAX_ENTRIES):
            oldest = min(_cache, key=lambda item: _cache[item][0])
            _cache.pop(oldest, None)


def _ttl_seconds() -> float:
    base = float(settings.RETRIEVAL_CACHE_TTL_SECONDS)
    if base <= 0:
        return 0.0
    jitter = max(0.0, float(settings.RETRIEVAL_CACHE_TTL_JITTER_SECONDS))
    return max(0.0, base + random.uniform(-jitter, jitter))


def _serialize(outcome: SearchOutcome) -> str:
    serialized = {
        "query": outcome.query,
        "degraded": outcome.degraded,
        "notes": list(outcome.notes),
        "results": [{
            "source": hit.source_key,
            "source_label": hit.source_label,
            "id": hit.doc_id,
            "title": hit.title,
            "description": hit.text,
            "score": hit.score,
            "url": hit.url,
            "rank": hit.rank,
            "payload": hit.payload,
        } for hit in outcome.hits],
        "sources": [report.to_dict() for report in outcome.reports],
    }
    return _stable({
        "schema_version": 1,
        "created_at": time.time(),
        "outcome": serialized,
    })


def _deserialize(raw: str) -> SearchOutcome | None:
    try:
        data = json.loads(raw)
        if data.get("schema_version") != 1:
            return None
        value = data["outcome"]
        reports = [SourceReport(
            source_key=item["source"],
            display_name=item["label"],
            availability=Availability(item["status"]),
            hits=int(item.get("hits", 0)),
            latency_ms=int(item.get("latency_ms", 0)),
            detail=item.get("detail", ""),
            searched_docs=item.get("searched_docs"),
        ) for item in value.get("sources", [])]
        hits = [RetrievalHit(
            source_key=item["source"],
            source_label=item.get("source_label", item["source"]),
            doc_id=item["id"],
            title=item.get("title", ""),
            text=item.get("description", ""),
            score=float(item.get("score", 0)),
            url=item.get("url"),
            payload=dict(item.get("payload") or {}),
            rank=int(item.get("rank", 0)),
        ) for item in value.get("results", [])]
        return SearchOutcome(
            hits=hits,
            reports=reports,
            query=value.get("query", ""),
            degraded=bool(value.get("degraded", False)),
            notes=list(value.get("notes", [])),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


async def get_async(key: str) -> SearchOutcome | None:
    local = get_local(key)
    if local is not None:
        return local
    client = await _redis_client()
    if client is None:
        return None
    try:
        raw = await client.get(key)
        if not raw:
            return None
        outcome = _deserialize(raw)
        if outcome is not None:
            put_local(key, outcome)
        return outcome
    except Exception:
        return None


async def put_async(key: str, outcome: SearchOutcome) -> None:
    put_local(key, outcome)
    ttl = _ttl_seconds()
    if ttl <= 0:
        return
    client = await _redis_client()
    if client is None:
        return
    try:
        await client.set(key, _serialize(outcome), ex=max(1, int(ttl)))
    except Exception:
        return


async def acquire_lock(key: str) -> str | None:
    client = await _redis_client()
    if client is None:
        return None
    token = uuid.uuid4().hex
    try:
        acquired = await client.set(
            f"{key}:lock", token, nx=True,
            ex=max(1, int(settings.RETRIEVAL_CACHE_LOCK_SECONDS)),
        )
        return token if acquired else None
    except Exception:
        return None


async def release_lock(key: str, token: str | None) -> None:
    if not token:
        return
    client = await _redis_client()
    if client is None:
        return
    try:
        current = await client.get(f"{key}:lock")
        if current == token:
            await client.delete(f"{key}:lock")
    except Exception:
        return


def get(key: str) -> SearchOutcome | None:
    """Backwards-compatible local-only read for tests and non-async callers."""
    return get_local(key)


def put(key: str, outcome: SearchOutcome) -> None:
    """Backwards-compatible local-only write for tests and non-async callers."""
    put_local(key, outcome)


def clear() -> None:
    with _lock:
        _cache.clear()


def workspace_update(
    workspace: dict | None,
    *,
    query: str,
    outcome: SearchOutcome,
) -> dict:
    """Keep compact immutable evidence references, not the full sensitive payload."""
    current = copy.deepcopy(workspace or {})
    searches = list(current.get("searches") or [])
    searches.append({
        "query": query[:2000],
        "sources_used": list(outcome.sources_used),
        "degraded": outcome.degraded,
        "timestamp": time.time(),
    })
    current["searches"] = searches[-max(1, settings.RETRIEVAL_WORKSPACE_MAX_SEARCHES):]
    hits = list(current.get("hits") or [])
    known = {(str(item.get("source")), str(item.get("id"))) for item in hits}
    for hit in outcome.hits:
        identity = (hit.source_key, hit.doc_id)
        if identity in known:
            continue
        hits.append({
            "source": hit.source_key,
            "id": hit.doc_id,
            "title": hit.title[:512],
            "rank": hit.rank,
        })
        known.add(identity)
    current["hits"] = hits[-max(1, settings.RETRIEVAL_WORKSPACE_MAX_HITS):]
    current["version"] = 1
    return current


def cache_stats() -> dict[str, int]:
    with _lock:
        return {"entries": len(_cache)}
