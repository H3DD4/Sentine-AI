"""
Source registry and health checking.

This module is the ONE place that knows which knowledge sources exist.  The
orchestrator, the sync jobs, the health endpoint and the UI all read from here,
so registering a new source in `_SOURCES` makes it visible to every layer at
once — no retrieval or API code needs to change.

The health checker is what turns the user's two requirements into enforced
behaviour rather than good intentions:

  * It records, per source, how many documents Postgres holds and how many
    vectors Qdrant holds.  That is what the UI displays so an analyst can see
    exactly which corpora are live.

  * It classifies each source as ready / empty / drifted / unavailable, which
    the orchestrator consults *before* fanning out.  A source that is down is
    skipped and reported, never allowed to fail the whole query.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.kb.base import Availability, KBSource
from app.kb.models import KBSourceState
from app.kb.sources import (
    FindingTemplatesSource,
    GhostwriterSource,
    InternalSource,
    MitreSource,
    NVDSource,
    OwaspDocsSource,
    OwaspSource,
)

log = logging.getLogger(__name__)


# ── The registry ─────────────────────────────────────────────────────────────
# Order matters only for display. Add a new source here and it becomes
# available to retrieval, sync, health checks and the UI simultaneously.

_SOURCES: list[KBSource] = [
    NVDSource(),
    MitreSource(),
    OwaspSource(),
    OwaspDocsSource(),
    GhostwriterSource(),
    FindingTemplatesSource(),
    InternalSource(),
]

_BY_KEY: dict[str, KBSource] = {s.key: s for s in _SOURCES}


def all_sources() -> list[KBSource]:
    return list(_SOURCES)


def get_source(key: str) -> KBSource:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise KeyError(
            f"Unknown KB source '{key}'. Registered: {sorted(_BY_KEY)}"
        ) from None


def source_keys() -> list[str]:
    return [s.key for s in _SOURCES]


def resolve_sources(keys: Optional[Iterable[str]] = None) -> list[KBSource]:
    """
    Resolve a caller-supplied source filter.  `None` means "all sources" —
    the default, because the analyst should get everything the firm knows
    unless they deliberately narrow the search.

    Unknown keys are logged and skipped rather than raising: a stale key in a
    saved UI filter must not break search.
    """
    if keys is None:
        return all_sources()
    resolved = []
    for k in keys:
        src = _BY_KEY.get(k)
        if src is None:
            log.warning("Ignoring unknown KB source filter: %s", k)
            continue
        resolved.append(src)
    return resolved


# ── Health state ─────────────────────────────────────────────────────────────


class SourceHealth:
    """
    Snapshot of one source's operational state.

    `availability` is what the orchestrator acts on:

      OK          → query it normally
      EMPTY       → skip (nothing indexed yet); report as benign, not an error
      DEGRADED    → still query it, but tell the user coverage is partial
      UNAVAILABLE → skip and report as a failure
      DISABLED    → skip; an operator turned it off deliberately
    """

    #: A source is called drifted when this fraction of its rows lack vectors.
    #: Below the threshold we still query it (partial coverage beats none) but
    #: we surface the gap; the exact number is shown, not just the flag.
    DRIFT_TOLERANCE = 0.02

    def __init__(
        self,
        source: KBSource,
        *,
        enabled: bool = True,
        row_count: int = 0,
        vector_count: int = 0,
        unsynced_count: int = 0,
        collection_exists: bool = True,
        error: str = "",
    ):
        self.source = source
        self.enabled = enabled
        self.row_count = row_count
        self.vector_count = vector_count
        self.unsynced_count = unsynced_count
        self.collection_exists = collection_exists
        self.error = error
        self.checked_at = datetime.utcnow()

    @property
    def key(self) -> str:
        return self.source.key

    @property
    def searchable_docs(self) -> int:
        """Only vectors are searchable — Postgres rows without one are invisible."""
        return self.vector_count

    @property
    def availability(self) -> Availability:
        if not self.enabled:
            return Availability.DISABLED
        if self.error:
            return Availability.UNAVAILABLE
        if not self.collection_exists:
            # A missing collection means different things depending on whether
            # there is data to index. With no rows the source has simply never
            # been synced — benign, and reporting it as a failure would put a
            # permanent "unavailable" warning on every query until someone
            # configures that feed, which trains the analyst to ignore the
            # warning that matters. With rows present the data exists but is
            # unsearchable, which is a genuine fault.
            return (
                Availability.UNAVAILABLE if self.row_count else Availability.EMPTY
            )
        if self.vector_count == 0:
            return Availability.EMPTY
        # Drift in either direction means the two stores disagree about what
        # the corpus contains, so results may be incomplete or unrenderable.
        if self.row_count and self.unsynced_count:
            if self.unsynced_count / max(self.row_count, 1) > self.DRIFT_TOLERANCE:
                return Availability.DEGRADED
        if self.row_count and self.vector_count < self.row_count * (1 - self.DRIFT_TOLERANCE):
            return Availability.DEGRADED
        return Availability.OK

    @property
    def detail(self) -> str:
        av = self.availability
        if av == Availability.DISABLED:
            return "disabled by operator"
        if av == Availability.UNAVAILABLE:
            # A probe failure outranks everything below it. When Postgres or
            # Qdrant is unreachable, `row_count` and `collection_exists` are not
            # observations — they are the defaults left behind by a probe that
            # never ran. Reporting them as fact told the analyst to reindex a
            # source whose database was simply down, which is a wasted hour
            # and a misleading audit record.
            if self.error:
                return self.error
            if not self.collection_exists:
                return (
                    f"{self.row_count} documents are in the database but the "
                    f"vector collection '{self.source.collection}' is missing — "
                    "they cannot be searched until this source is reindexed"
                )
            return "backend unreachable"
        if av == Availability.EMPTY:
            if not self.collection_exists:
                return "not configured yet — no documents and no index"
            return "no documents indexed yet"
        if av == Availability.DEGRADED:
            if self.unsynced_count:
                return (
                    f"{self.unsynced_count} of {self.row_count} documents are not yet "
                    "vectorised — coverage is partial"
                )
            return (
                f"{self.vector_count} vectors vs {self.row_count} database rows — "
                "stores disagree, coverage is partial"
            )
        return f"{self.vector_count} documents indexed"

    def to_dict(self) -> dict:
        return {
            "source": self.key,
            "label": self.source.display_name,
            "collection": self.source.collection,
            "table": self.source.table,
            "status": self.availability.value,
            "enabled": self.enabled,
            "detail": self.detail,
            "row_count": self.row_count,
            "vector_count": self.vector_count,
            "unsynced_count": self.unsynced_count,
            "weight": self.source.weight,
            "checked_at": self.checked_at.isoformat(),
        }


async def check_source_health(
    source: KBSource,
    session: AsyncSession,
    qdrant,
    *,
    known_collections: Optional[set[str]] = None,
    enabled: bool = True,
) -> SourceHealth:
    """
    Probe one source.  Never raises — a failing probe *is* the answer, and the
    orchestrator needs that answer to route around the source.
    """
    import asyncio

    row_count = 0
    unsynced = 0
    vector_count = 0
    collection_exists = True
    error = ""

    # Postgres side. A missing table (pre-migration) is reported, not raised.
    try:
        row_count = await source.row_count(session)
        unsynced = await source.unsynced_count(session)
    except Exception as exc:
        error = f"database error: {type(exc).__name__}: {exc}"
        log.warning("Health check DB probe failed for %s: %s", source.key, exc)
        # A failed DB probe must not leave the session poisoned for the next
        # source's probe in the same loop.
        try:
            await session.rollback()
        except Exception:
            pass

    # Qdrant side.
    try:
        if known_collections is not None:
            collection_exists = source.collection in known_collections
        else:
            collection_exists = await asyncio.to_thread(
                qdrant.collection_exists, source.collection
            )
        if collection_exists:
            info = await asyncio.to_thread(qdrant.count, source.collection, exact=True)
            vector_count = int(info.count)
    except Exception as exc:
        collection_exists = False
        error = error or f"vector store error: {type(exc).__name__}: {exc}"
        log.warning("Health check Qdrant probe failed for %s: %s", source.key, exc)

    return SourceHealth(
        source,
        enabled=enabled,
        row_count=row_count,
        vector_count=vector_count,
        unsynced_count=unsynced,
        collection_exists=collection_exists,
        error=error,
    )


async def check_all_sources(
    session: AsyncSession,
    qdrant,
    *,
    persist: bool = True,
) -> dict[str, SourceHealth]:
    """
    Probe every registered source.  Results are cached in-process (see
    `get_cached_health`) so the retrieval hot path does not re-probe on every
    query, and optionally persisted to `kb_source_state` for the UI.
    """
    import asyncio

    # One listing call instead of N existence checks.
    known: Optional[set[str]] = None
    try:
        cols = await asyncio.to_thread(qdrant.get_collections)
        known = {c.name for c in cols.collections}
    except Exception as exc:
        log.warning("Could not list Qdrant collections: %s", exc)

    enabled_map = await _load_enabled_flags(session)

    out: dict[str, SourceHealth] = {}
    for src in _SOURCES:
        out[src.key] = await check_source_health(
            src,
            session,
            qdrant,
            known_collections=known,
            enabled=enabled_map.get(src.key, True),
        )

    _cache_health(out)

    if persist:
        try:
            await _persist_health(session, out)
        except Exception as exc:
            log.warning("Could not persist source health: %s", exc)
            try:
                await session.rollback()
            except Exception:
                pass

    return out


async def _load_enabled_flags(session: AsyncSession) -> dict[str, bool]:
    from sqlalchemy import select

    try:
        rows = (await session.execute(select(KBSourceState))).scalars().all()
        return {r.source_key: bool(r.enabled) for r in rows}
    except Exception:
        # Table may not exist yet (first boot, pre-migration). Default to
        # enabled so a fresh install is not silently inert.
        try:
            await session.rollback()
        except Exception:
            pass
        return {}


async def _persist_health(session: AsyncSession, health: dict[str, SourceHealth]) -> None:
    now = datetime.utcnow()
    for key, h in health.items():
        row = await session.get(KBSourceState, key)
        if row is None:
            row = KBSourceState(source_key=key, display_name=h.source.display_name)
            session.add(row)
        row.display_name = h.source.display_name
        row.status = h.availability.value
        row.row_count = h.row_count
        row.vector_count = h.vector_count
        row.unsynced_count = h.unsynced_count
        row.last_checked_at = now
        row.last_error = h.error or None
    await session.commit()


# ── In-process health cache ──────────────────────────────────────────────────
# Retrieval consults this instead of re-probing Qdrant on every keystroke.
# Short TTL: long enough to keep the hot path fast, short enough that a source
# coming back online is picked up without a restart.

_HEALTH_CACHE: dict[str, SourceHealth] = {}
_HEALTH_CACHED_AT: float = 0.0
HEALTH_TTL_SECONDS = 30.0


def _cache_health(health: dict[str, SourceHealth]) -> None:
    global _HEALTH_CACHE, _HEALTH_CACHED_AT
    _HEALTH_CACHE = health
    _HEALTH_CACHED_AT = time.monotonic()


def get_cached_health() -> Optional[dict[str, SourceHealth]]:
    if not _HEALTH_CACHE:
        return None
    if time.monotonic() - _HEALTH_CACHED_AT > HEALTH_TTL_SECONDS:
        return None
    return _HEALTH_CACHE


def invalidate_health_cache() -> None:
    """Call after a sync or ingest so the next query sees fresh counts."""
    global _HEALTH_CACHE, _HEALTH_CACHED_AT
    _HEALTH_CACHE = {}
    _HEALTH_CACHED_AT = 0.0


async def get_health(
    session: AsyncSession, qdrant, *, force: bool = False
) -> dict[str, SourceHealth]:
    """Cached health lookup — the entry point retrieval uses."""
    if not force:
        cached = get_cached_health()
        if cached is not None:
            return cached
    return await check_all_sources(session, qdrant, persist=False)
