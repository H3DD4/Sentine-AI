"""
Federated multi-source retrieval.

This is the multi-RAG orchestrator.  For one user query it runs, per source:

    arm 1  exact-ID lookup   — regex-extracted identifiers (CVE-…, T1059.001)
    arm 2  dense vector      — BGE semantic similarity
    arm 3  sparse BM25       — literal terms: versions, CPE strings, CWE numbers

Arms 2 and 3 are fused *inside* Qdrant with server-side RRF (one round trip,
not two).  The per-source results are then fused *across* sources with weighted
RRF, and the survivors are reranked by a cross-encoder.

Why RRF rather than blending scores
-----------------------------------
The previous implementation computed `0.7 * cosine + 0.3 * keyword_ratio`.
That is unsound: cosine similarity and a term-hit ratio are different
quantities on different scales, and BGE's cosine range shifts when the query
prefix is applied, so the 0.7/0.3 weights silently change meaning.  RRF
consumes only *ranks*, so it is invariant to score scale and calibration — the
standard choice for fusing heterogeneous retrievers.

Why the exact-ID arm exists
---------------------------
An analyst pasting "CVE-2023-50164" wants that record, deterministically.
Neither dense nor sparse retrieval guarantees rank-1 for a rare identifier, so
identifiers are looked up directly and pinned above the fused results.

Graceful degradation
--------------------
Every arm is wrapped so that a failing source degrades the answer instead of
failing it.  A source that is down, empty or drifted is skipped, and a
SourceReport records exactly why.  `SearchOutcome.provenance_line()` puts that
into both the UI and the LLM prompt, so neither the analyst nor the model can
mistake partial coverage for complete coverage.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional, Sequence

from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    FusionQuery,
    MatchAny,
    MatchValue,
    Prefetch,
    Range,
    SparseVector as QdrantSparseVector,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.ingestion.embedder import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    embed_query,
    sparse_embed_query,
)
from app.kb.base import (
    Availability,
    KBSource,
    RetrievalHit,
    SearchOutcome,
    SourceReport,
)
from app.kb.registry import get_health, resolve_sources

log = logging.getLogger(__name__)

#: Candidates each arm pulls before fusion. Generous, because rerankers can
#: only reorder what retrieval surfaced — recall lost here is unrecoverable.
PREFETCH_LIMIT = 40
#: Candidates kept per source after in-collection RRF.
PER_SOURCE_LIMIT = 15
#: Documents handed to the cross-encoder.
RERANK_CANDIDATES = 40
#: Documents returned to the caller.
FINAL_TOP_K = 8

#: RRF constant. 60 is the value from the original Cormack et al. paper and the
#: Qdrant default; it damps the influence of any single arm's top rank.
RRF_K = 60

#: Cross-encoder relevance floor. Calibrated by the eval harness — a fixed
#: guess here is how a RAG ends up citing irrelevant documents confidently.
#: Logit scale, not cosine: ~0 is the decision boundary for bge-reranker.
RERANK_MIN_SCORE = -2.0


# ── Qdrant client ────────────────────────────────────────────────────────────

_qdrant_client: Optional[QdrantClient] = None


def init_qdrant_client() -> None:
    global _qdrant_client
    _qdrant_client = QdrantClient(url=settings.QDRANT_URL, timeout=30)


def get_qdrant() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        init_qdrant_client()
    return _qdrant_client


# ── Cross-encoder reranker ───────────────────────────────────────────────────

_reranker = None
_reranker_failed = False
#: Why the reranker is not in use, in analyst-facing words. Empty when it is
#: working. Surfaced through SearchOutcome so a silently-degraded ranking is
#: never presented as a fully-ranked one.
_reranker_note = ""


def reranker_status() -> str:
    """'' when reranking is live, else the reason it is not."""
    return _reranker_note


def load_reranker_sync() -> None:
    """
    Load the cross-encoder. Optional by design: if it cannot load, retrieval
    continues on fused RRF order rather than failing.

    Loading is offline-only unless `ALLOW_MODEL_DOWNLOADS` is set. An uncached
    model raises immediately here instead of blocking the caller on a download,
    which is the difference between "this query ranked slightly worse" and
    "this query never came back".
    """
    global _reranker, _reranker_failed, _reranker_note
    if _reranker is not None or _reranker_failed:
        return
    try:
        from sentence_transformers import CrossEncoder

        name = getattr(settings, "RERANKER_MODEL", "BAAI/bge-reranker-base")
        offline = not getattr(settings, "ALLOW_MODEL_DOWNLOADS", False)
        log.info("Loading reranker: %s (offline=%s) …", name, offline)
        _reranker = CrossEncoder(
            name, model_kwargs={"local_files_only": offline} if offline else {}
        )
        _reranker_note = ""
        log.info("Reranker ready.")
    except Exception as exc:
        _reranker_failed = True
        _reranker_note = (
            "results are ranked by hybrid fusion score; the cross-encoder "
            "reranker is not available on this host"
        )
        log.warning("Reranker unavailable, falling back to RRF order: %s", exc)


def _rerank_sync(query: str, hits: list[RetrievalHit]) -> list[RetrievalHit]:
    load_reranker_sync()
    if _reranker is None or not hits:
        return hits

    pairs = [(query, f"{h.title}\n{h.text}"[:2000]) for h in hits]
    try:
        scores = _reranker.predict(pairs)
    except Exception as exc:
        log.warning("Rerank failed, keeping RRF order: %s", exc)
        return hits

    for h, s in zip(hits, scores):
        h.score = float(s)

    kept = [h for h in hits if h.score >= RERANK_MIN_SCORE]
    # If the floor rejects everything, the query genuinely has no good match.
    # Returning the single best candidate (rather than nothing) lets the LLM
    # see it and say "this is weak" — but we do not pretend it passed.
    if not kept and hits:
        kept = [max(hits, key=lambda h: h.score)]

    kept.sort(key=lambda h: h.score, reverse=True)
    return kept


# ── Filters ──────────────────────────────────────────────────────────────────


def _build_filter(source: KBSource, cvss_min: float, extra: Optional[dict]) -> Optional[Filter]:
    must: list[Any] = []

    if cvss_min > 0:
        # Only meaningful where the source actually carries CVSS.
        if source.key == "nvd":
            must.append(FieldCondition(key="cvss_v3", range=Range(gte=cvss_min)))
        elif source.key == "ghostwriter":
            must.append(FieldCondition(key="cvss_score", range=Range(gte=cvss_min)))

    if source.key == "mitre":
        # Deprecated techniques would mislead a report.
        must.append(FieldCondition(key="deprecated", match=MatchValue(value=False)))

    for key, value in (extra or {}).items():
        if value is None or value == [] or value == "":
            continue
        if isinstance(value, (list, tuple, set)):
            must.append(FieldCondition(key=key, match=MatchAny(any=list(value))))
        else:
            must.append(FieldCondition(key=key, match=MatchValue(value=value)))

    return Filter(must=must) if must else None


# ── Single-source retrieval ──────────────────────────────────────────────────


def _hybrid_query_sync(
    qdrant: QdrantClient,
    source: KBSource,
    dense_vec: list[float],
    sparse_vec,
    qfilter: Optional[Filter],
    limit: int,
) -> list[Any]:
    """
    One round trip: dense + sparse prefetch fused server-side by RRF.

    Doing the fusion in Qdrant rather than the app avoids shipping two result
    sets over the wire and keeps the fusion consistent with the engine's own
    ranking.
    """
    prefetch = [
        Prefetch(
            query=dense_vec,
            using=DENSE_VECTOR_NAME,
            limit=PREFETCH_LIMIT,
            filter=qfilter,
        )
    ]
    if sparse_vec is not None and not sparse_vec.is_empty():
        prefetch.append(
            Prefetch(
                query=QdrantSparseVector(
                    indices=sparse_vec.indices, values=sparse_vec.values
                ),
                using=SPARSE_VECTOR_NAME,
                limit=PREFETCH_LIMIT,
                filter=qfilter,
            )
        )

    return qdrant.query_points(
        collection_name=source.collection,
        prefetch=prefetch,
        query=FusionQuery(fusion="rrf"),
        limit=limit,
        with_payload=True,
    ).points


def _exact_id_query_sync(
    qdrant: QdrantClient,
    source: KBSource,
    doc_ids: list[str],
    qfilter: Optional[Filter],
) -> list[Any]:
    """Direct payload lookup for identifiers found verbatim in the query."""
    must = [FieldCondition(key="doc_id", match=MatchAny(any=doc_ids))]
    if qfilter:
        must.extend(qfilter.must or [])
    res = qdrant.query_points(
        collection_name=source.collection,
        query_filter=Filter(must=must),
        limit=len(doc_ids) * 4,  # a doc may have several chunks
        with_payload=True,
    )
    return res.points


def _dedupe_best_chunk(points: Sequence[Any]) -> list[Any]:
    """
    Collapse multiple chunks of one document to its best-scoring chunk.

    Chunk-level indexing is what makes long documents retrievable, but the
    caller wants documents, and letting one verbose CVE occupy five result slots
    would crowd out other sources.
    """
    best: dict[str, Any] = {}
    for p in points:
        doc_id = (p.payload or {}).get("doc_id")
        if not doc_id:
            continue
        prev = best.get(doc_id)
        if prev is None or (p.score or 0) > (prev.score or 0):
            best[doc_id] = p
    return sorted(best.values(), key=lambda p: p.score or 0, reverse=True)


async def _search_one_source(
    source: KBSource,
    query: str,
    dense_vec: list[float],
    sparse_vec,
    *,
    qdrant: QdrantClient,
    health,
    cvss_min: float,
    payload_filter: Optional[dict],
) -> tuple[list[RetrievalHit], SourceReport]:
    """
    Query one source. Never raises — returns an explanatory SourceReport
    instead, so one broken source cannot take down the whole search.
    """
    started = time.monotonic()

    def report(av: Availability, hits: int = 0, detail: str = "") -> SourceReport:
        return SourceReport(
            source_key=source.key,
            display_name=source.display_name,
            availability=av,
            hits=hits,
            latency_ms=int((time.monotonic() - started) * 1000),
            detail=detail,
            searched_docs=getattr(health, "searchable_docs", None) if health else None,
        )

    # Pre-flight: skip sources the health check already knows cannot answer.
    if health is not None:
        av = health.availability
        if av in (Availability.DISABLED, Availability.EMPTY, Availability.UNAVAILABLE):
            return [], report(av, detail=health.detail)

    try:
        exact_ids = source.extract_ids(query)

        tasks = [
            asyncio.to_thread(
                _hybrid_query_sync,
                qdrant,
                source,
                dense_vec,
                sparse_vec,
                _build_filter(source, cvss_min, payload_filter),
                PER_SOURCE_LIMIT * 3,  # room for chunk collapsing
            )
        ]
        if exact_ids:
            tasks.append(
                asyncio.to_thread(
                    _exact_id_query_sync,
                    qdrant,
                    source,
                    exact_ids,
                    _build_filter(source, cvss_min, payload_filter),
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as exc:
        log.warning("Source %s failed: %s", source.key, exc)
        return [], report(Availability.UNAVAILABLE, detail=f"{type(exc).__name__}: {exc}")

    fused_points: list[Any] = []
    exact_points: list[Any] = []

    if isinstance(results[0], Exception):
        log.warning("Hybrid arm failed for %s: %s", source.key, results[0])
        return [], report(
            Availability.UNAVAILABLE,
            detail=f"{type(results[0]).__name__}: {results[0]}",
        )
    fused_points = list(results[0])

    if len(results) > 1:
        if isinstance(results[1], Exception):
            # The exact-ID arm is a bonus; losing it is not a source failure.
            log.debug("Exact-ID arm failed for %s: %s", source.key, results[1])
        else:
            exact_points = list(results[1])

    hits: list[RetrievalHit] = []
    seen: set[str] = set()

    # Exact-ID matches are pinned first: if the analyst named a document, it
    # belongs in the context regardless of what the vector arms ranked.
    for rank, p in enumerate(_dedupe_best_chunk(exact_points)):
        payload = p.payload or {}
        doc_id = payload.get("doc_id")
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        hit = source.hit_from_payload(payload, 1.0)
        hit.rank = rank
        hit.payload["matched_by"] = "exact_id"
        hit.text = payload.get("chunk_text") or hit.text
        hits.append(hit)

    for p in _dedupe_best_chunk(fused_points):
        payload = p.payload or {}
        doc_id = payload.get("doc_id")
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        hit = source.hit_from_payload(payload, float(p.score or 0.0))
        hit.rank = len(hits)
        hit.payload["matched_by"] = "hybrid"
        hit.text = payload.get("chunk_text") or hit.text
        hits.append(hit)
        if len(hits) >= PER_SOURCE_LIMIT:
            break

    if not hits:
        return [], report(Availability.NO_MATCH, detail="no documents matched this query")

    # A drifted source did answer, but its coverage is incomplete — surface
    # that rather than implying a complete search.
    if health is not None and health.availability == Availability.DEGRADED:
        return hits, report(Availability.DEGRADED, len(hits), health.detail)

    return hits, report(Availability.OK, len(hits), f"{len(hits)} matches")


# ── Cross-source fusion ──────────────────────────────────────────────────────


def _weighted_rrf(
    per_source: list[tuple[KBSource, list[RetrievalHit]]]
) -> list[RetrievalHit]:
    """
    Fuse per-source rankings with weighted Reciprocal Rank Fusion.

        score(d) = Σ_sources  weight_s / (RRF_K + rank_s(d))

    Rank-based, so a source whose scores happen to run high cannot dominate;
    weights express editorial trust (firm findings over public feeds) rather
    than score calibration.
    """
    scored: list[tuple[float, RetrievalHit]] = []
    for source, hits in per_source:
        for rank, hit in enumerate(hits):
            # Exact-ID matches enter fusion at rank 0 so a named document
            # cannot be pushed out by semantically similar neighbours.
            effective_rank = 0 if hit.payload.get("matched_by") == "exact_id" else rank
            scored.append((source.weight / (RRF_K + effective_rank + 1), hit))

    merged: dict[tuple[str, str], tuple[float, RetrievalHit]] = {}
    for s, hit in scored:
        key = (hit.source_key, hit.doc_id)
        prev = merged.get(key)
        if prev is None:
            merged[key] = (s, hit)
        else:
            merged[key] = (prev[0] + s, prev[1])

    ordered = sorted(merged.values(), key=lambda t: t[0], reverse=True)
    out = []
    for rank, (s, hit) in enumerate(ordered):
        hit.score = s
        hit.rank = rank
        out.append(hit)
    return out


# ── Public API ───────────────────────────────────────────────────────────────


async def federated_search(
    query: str,
    session: AsyncSession,
    *,
    sources: Optional[Sequence[str]] = None,
    cvss_min: float = 0.0,
    payload_filter: Optional[dict] = None,
    top_k: int = FINAL_TOP_K,
    rerank: bool = True,
) -> SearchOutcome:
    """
    Run the full multi-source retrieval pipeline for one query.

    Returns a SearchOutcome carrying both the hits and the per-source
    provenance record — callers must surface the latter to the analyst.
    """
    query = (query or "").strip()
    if not query:
        return SearchOutcome(query=query)

    qdrant = get_qdrant()
    selected = resolve_sources(sources)
    if not selected:
        return SearchOutcome(query=query)

    health_map = await get_health(session, qdrant)

    # Embed once and reuse across every source — the dominant CPU cost.
    notes: list[str] = []

    dense_vec = await asyncio.to_thread(embed_query, query)
    try:
        sparse_vec = await asyncio.to_thread(sparse_embed_query, query)
    except Exception as exc:
        log.warning("Sparse query embedding failed, dense-only this query: %s", exc)
        sparse_vec = None
        # Worth telling the analyst: the sparse arm is what recalls exact
        # identifiers (CVE-2020-1938, T1059.001). Without it, a query that is
        # mostly an identifier may miss the very document that names it, and
        # that is not something the answer text would otherwise reveal.
        notes.append(
            "exact-identifier (BM25) matching was unavailable — searched by "
            "semantic similarity only"
        )

    arms = await asyncio.gather(
        *[
            _search_one_source(
                src,
                query,
                dense_vec,
                sparse_vec,
                qdrant=qdrant,
                health=health_map.get(src.key),
                cvss_min=cvss_min,
                payload_filter=payload_filter,
            )
            for src in selected
        ],
        return_exceptions=True,
    )

    per_source: list[tuple[KBSource, list[RetrievalHit]]] = []
    reports: list[SourceReport] = []

    for src, outcome in zip(selected, arms):
        if isinstance(outcome, Exception):
            log.error("Unexpected failure in source %s: %s", src.key, outcome)
            reports.append(
                SourceReport(
                    source_key=src.key,
                    display_name=src.display_name,
                    availability=Availability.UNAVAILABLE,
                    detail=f"{type(outcome).__name__}: {outcome}",
                )
            )
            continue
        hits, report = outcome
        reports.append(report)
        if hits:
            per_source.append((src, hits))

    fused = _weighted_rrf(per_source)

    if rerank and settings.RERANK_ENABLED and fused:
        fused = await asyncio.to_thread(_rerank_sync, query, fused[:RERANK_CANDIDATES])
        # Checked *after* the attempt, since the loader resolves lazily on
        # first use — asking before this point would always report "fine".
        note = reranker_status()
        if note:
            notes.append(note)

    final = fused[:top_k]

    # Report counts reflect what actually survived into the answer, so the
    # provenance line cannot claim a source contributed when it was cut.
    surviving: dict[str, int] = {}
    for h in final:
        surviving[h.source_key] = surviving.get(h.source_key, 0) + 1
    for r in reports:
        if r.availability in (Availability.OK, Availability.DEGRADED):
            r.hits = surviving.get(r.source_key, 0)
            if r.hits == 0 and r.availability == Availability.OK:
                r.availability = Availability.NO_MATCH
                r.detail = "matches found but outranked by other sources"

    return SearchOutcome(
        hits=final,
        reports=reports,
        query=query,
        degraded=any(r.is_failure for r in reports),
        notes=notes,
    )


async def multimodal_search(
    description: str,
    session: AsyncSession,
    *,
    evidence_texts: Optional[Sequence[str]] = None,
    image_descriptions: Optional[Sequence[str]] = None,
    sources: Optional[Sequence[str]] = None,
    cvss_min: float = 0.0,
    top_k: int = FINAL_TOP_K,
) -> SearchOutcome:
    """
    Retrieve over every modality of a finding, not just its prose description.

    Each modality becomes its own query (see `query_planner` for why they are
    not concatenated), the queries run concurrently across all sources, and
    the result lists are fused by rank.  The returned outcome carries a single
    merged provenance record, so the analyst still sees one coherent statement
    of what was searched even though several queries were issued.
    """
    from app.services.query_planner import fuse_outcomes, plan_queries

    planned = plan_queries(description, evidence_texts, image_descriptions)
    if not planned:
        return SearchOutcome(query=description or "")

    if len(planned) == 1:
        # Single modality: no cross-query fusion to do, and going through the
        # fuser would needlessly rewrite scores that are already comparable.
        return await federated_search(
            planned[0].text,
            session,
            sources=sources,
            cvss_min=cvss_min,
            top_k=top_k,
        )

    results = await asyncio.gather(
        *[
            federated_search(
                q.text,
                session,
                sources=sources,
                cvss_min=cvss_min,
                # Each query contributes a deeper list than the final cut, so
                # a document ranked mid-list by several modalities can still
                # win on agreement — the main reason to fuse rather than to
                # concatenate top-k lists.
                top_k=max(top_k * 2, RERANK_CANDIDATES // 2),
            )
            for q in planned
        ],
        return_exceptions=True,
    )

    pairs = []
    for q, res in zip(planned, results):
        if isinstance(res, Exception):
            log.error("Query arm '%s' failed: %s", q.modality, res)
            continue
        pairs.append((q, res))

    if not pairs:
        return SearchOutcome(query=description or "", degraded=True)

    outcome = fuse_outcomes(pairs, top_k=top_k)
    outcome.query = description or ""
    log.info(
        "Multimodal search: %d queries (%s) → %d hits from %s",
        len(pairs),
        ", ".join(q.modality for q, _ in pairs),
        len(outcome.hits),
        ", ".join(outcome.sources_used) or "no source",
    )
    return outcome


async def hybrid_search(
    query: str,
    session: AsyncSession,
    cvss_min: float = 0.0,
    entry_types: list[str] | None = None,
) -> list[dict]:
    """
    Backwards-compatible wrapper for existing call sites.

    Maps the old `entry_types` vocabulary ("cve", "attack_vector", "internal")
    onto the new source keys, so chat/validation keep working unchanged during
    the migration. New code should call `federated_search` and surface the
    provenance report.
    """
    legacy_map = {
        "cve": "nvd",
        "attack_vector": "mitre",
        "technique": "mitre",
        "internal": "internal",
        "finding": "ghostwriter",
    }
    mapped = None
    if entry_types:
        mapped = list({legacy_map.get(t, t) for t in entry_types})

    outcome = await federated_search(
        query, session, sources=mapped, cvss_min=cvss_min
    )
    return [h.to_dict() for h in outcome.hits]
