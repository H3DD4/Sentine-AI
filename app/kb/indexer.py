"""
Shared indexer — writes any KBSource's rows into its own Qdrant collection.

One indexer serves every source.  A new source needs no indexing code: it
implements `build_text` / `build_payload` and this module handles chunking,
dense + sparse embedding, deterministic point IDs, stale-chunk cleanup and
sync-state writeback.

Two properties this module guarantees, both of which the previous ingestion
path lacked and which together caused the 10,459-vectors / 0-rows split-brain:

*   **Postgres is committed before Qdrant is marked synced.**  The old NVD sync
    upserted vectors inside the pagination loop but committed rows only after
    the whole loop finished, so a mid-run failure persisted every vector and
    rolled back every row.  Here, each batch is committed, and
    `qdrant_synced_at` is stamped only after the vector upsert for that batch
    returns.  A crash leaves rows with a NULL timestamp — recoverable, and
    visible to the health check as drift.

*   **Re-indexing is idempotent.**  Point IDs derive from (doc_id, chunk_index),
    so re-running overwrites in place instead of duplicating, and shrinking a
    document deletes the chunks it no longer has.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Iterable, Optional, Sequence

from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    Range,
    SparseVector as QdrantSparseVector,
    SparseVectorParams,
    VectorParams,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.embedder import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    chunk_text_with_header,
    content_hash,
    current_embed_signature,
    deterministic_qdrant_id,
    embed_documents,
    embedding_dim,
    sparse_embed_documents,
)
from app.kb.base import KBSource
from app.kb.registry import invalidate_health_cache

log = logging.getLogger(__name__)

#: Payload fields indexed in every collection. Without these, Qdrant filters
#: fall back to a full scan — the existing collection has `payload_schema: {}`
#: and `indexed_vectors_count: 0`, i.e. every search today is brute force.
_COMMON_INDEXES: dict[str, PayloadSchemaType] = {
    "doc_id": PayloadSchemaType.KEYWORD,
    "source": PayloadSchemaType.KEYWORD,
}

#: Extra indexes per source, for the filters that source actually supports.
_SOURCE_INDEXES: dict[str, dict[str, PayloadSchemaType]] = {
    "nvd": {
        "cvss_v3": PayloadSchemaType.FLOAT,
        "severity": PayloadSchemaType.KEYWORD,
        "cwe": PayloadSchemaType.KEYWORD,
    },
    "mitre": {
        "tactics": PayloadSchemaType.KEYWORD,
        "platforms": PayloadSchemaType.KEYWORD,
        "deprecated": PayloadSchemaType.BOOL,
    },
    "owasp": {
        "rank": PayloadSchemaType.INTEGER,
        "year": PayloadSchemaType.INTEGER,
        "cwe_ids": PayloadSchemaType.KEYWORD,
    },
    "owasp_docs": {
        "project": PayloadSchemaType.KEYWORD,
        "version": PayloadSchemaType.KEYWORD,
        "cwe_ids": PayloadSchemaType.KEYWORD,
    },
    "ghostwriter": {
        "severity": PayloadSchemaType.KEYWORD,
        "finding_type": PayloadSchemaType.KEYWORD,
        "engagement_code": PayloadSchemaType.KEYWORD,
        "client_name": PayloadSchemaType.KEYWORD,
    },
    "internal": {
        "doc_type": PayloadSchemaType.KEYWORD,
        "tags": PayloadSchemaType.KEYWORD,
    },
    "finding_templates": {
        "template_code": PayloadSchemaType.KEYWORD,
        "record_kind": PayloadSchemaType.KEYWORD,
        "category": PayloadSchemaType.KEYWORD,
        "source_file": PayloadSchemaType.KEYWORD,
    },
}


# ── Collection lifecycle ─────────────────────────────────────────────────────


def ensure_collection_sync(qdrant, source: KBSource, *, dim: Optional[int] = None) -> bool:
    """
    Create the source's collection with named dense + sparse vectors if absent,
    and ensure payload indexes exist. Returns True if it created the collection.

    Named vectors are required for hybrid search: Qdrant fuses a dense and a
    sparse query server-side only when both live in the same collection under
    distinct names.
    """
    dim = dim or embedding_dim()
    created = False

    if not qdrant.collection_exists(source.collection):
        qdrant.create_collection(
            collection_name=source.collection,
            vectors_config={
                DENSE_VECTOR_NAME: VectorParams(size=dim, distance=Distance.COSINE),
            },
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: SparseVectorParams(),
            },
        )
        created = True
        log.info("Created Qdrant collection '%s' (dim=%d, +sparse).", source.collection, dim)

    indexes = {**_COMMON_INDEXES, **_SOURCE_INDEXES.get(source.key, {})}
    for field, schema in indexes.items():
        try:
            qdrant.create_payload_index(
                collection_name=source.collection,
                field_name=field,
                field_schema=schema,
            )
        except Exception as exc:
            # Already-exists is the common case and is not an error.
            if "already exists" not in str(exc).lower():
                log.debug("Payload index %s.%s: %s", source.collection, field, exc)

    return created


async def ensure_collection(qdrant, source: KBSource, *, dim: Optional[int] = None) -> bool:
    return await asyncio.to_thread(ensure_collection_sync, qdrant, source, dim=dim)


async def ensure_all_collections(qdrant, sources: Iterable[KBSource]) -> None:
    for src in sources:
        try:
            await ensure_collection(qdrant, src)
        except Exception as exc:
            log.warning("Could not ensure collection for %s: %s", src.key, exc)


async def rebuild_collection(qdrant, source: KBSource, *, dim: Optional[int] = None) -> None:
    """Recreate one source collection for an embedding dimension/model change."""
    exists = await asyncio.to_thread(qdrant.collection_exists, source.collection)
    if exists:
        await asyncio.to_thread(qdrant.delete_collection, source.collection)
    await ensure_collection(qdrant, source, dim=dim)


# ── Indexing ─────────────────────────────────────────────────────────────────


class IndexStats:
    def __init__(self) -> None:
        self.considered = 0
        self.indexed = 0
        self.skipped_unchanged = 0
        self.chunks = 0
        self.failed = 0

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<IndexStats considered={self.considered} indexed={self.indexed} "
            f"skipped={self.skipped_unchanged} chunks={self.chunks} failed={self.failed}>"
        )

    def to_dict(self) -> dict:
        return {
            "considered": self.considered,
            "indexed": self.indexed,
            "skipped_unchanged": self.skipped_unchanged,
            "chunks": self.chunks,
            "failed": self.failed,
        }


def _doc_id_of(source: KBSource, row: Any) -> str:
    return str(getattr(row, source.pk_column.key))


def _chunk_header(doc_id: str, payload: dict) -> str:
    """Compact identity metadata repeated on every independently retrieved chunk."""
    lines = [f"Document ID: {doc_id}"]
    title = payload.get("title")
    if title and str(title) != doc_id:
        lines.append(f"Title: {title}")

    severity = payload.get("severity")
    cvss = payload.get("cvss_v3") or payload.get("cvss_score")
    if severity or cvss:
        value = f"Severity: {severity or 'unknown'}"
        if cvss:
            value += f"; CVSS: {cvss}"
        lines.append(value)

    cwe = payload.get("cwe")
    if cwe:
        lines.append(f"Weakness: {cwe}")
    tactics = payload.get("tactics") or []
    if tactics:
        lines.append("Tactics: " + ", ".join(map(str, tactics)))
    return "\n".join(lines)


def _build_points(source: KBSource, row: Any, text: str) -> list[PointStruct]:
    """Chunk, embed (dense + sparse), and build one point per chunk."""
    return _build_points_batch(source, [(row, text)])[0][1]


def _build_points_batch(
    source: KBSource, items: Sequence[tuple[Any, str]]
) -> list[tuple[Any, list[PointStruct]]]:
    """
    Chunk and embed a whole batch of documents in one encoder pass.

    Embedding one document at a time leaves the transformer running at batch
    size 1, which for a corpus of short records like CVEs is where nearly all
    the wall-clock goes — the GPU/CPU is idle between calls rather than busy.
    Flattening every chunk of every document in the batch into a single
    `encode` call is the difference between minutes and an hour on a 10k
    backfill, and changes nothing about the vectors produced.
    """
    flat_texts: list[str] = []
    # (row, doc_id, payload, chunk_texts, slice into flat_texts)
    layout: list[tuple[Any, str, dict, list[str], slice]] = []

    for row, text in items:
        doc_id = _doc_id_of(source, row)
        base_payload = source.build_payload(row)
        header = _chunk_header(doc_id, base_payload)
        chunks = chunk_text_with_header(header, text)
        start = len(flat_texts)
        flat_texts.extend(chunks)
        layout.append((row, doc_id, base_payload, chunks, slice(start, len(flat_texts))))

    if not flat_texts:
        return [(row, []) for row, _ in items]

    dense_vecs = embed_documents(flat_texts)
    try:
        sparse_vecs = sparse_embed_documents(flat_texts)
    except Exception as exc:
        # The sparse arm is an enhancement, not a prerequisite. If BM25 is
        # unavailable we still index dense vectors so retrieval keeps working
        # in degraded form rather than failing the whole ingest.
        log.warning("Sparse embedding unavailable, indexing dense-only: %s", exc)
        sparse_vecs = [None] * len(flat_texts)

    results: list[tuple[Any, list[PointStruct]]] = []

    for row, doc_id, base_payload, chunks, span in layout:
        if not chunks:
            results.append((row, []))
            continue

        points: list[PointStruct] = []
        row_dense = dense_vecs[span]
        row_sparse = sparse_vecs[span]

        for idx, (chunk, dense_vec, sparse) in enumerate(
            zip(chunks, row_dense, row_sparse)
        ):
            vectors: dict[str, Any] = {DENSE_VECTOR_NAME: dense_vec}
            if sparse is not None and not sparse.is_empty():
                vectors[SPARSE_VECTOR_NAME] = QdrantSparseVector(
                    indices=sparse.indices, values=sparse.values
                )

            payload = dict(base_payload)
            payload.update(
                {
                    "doc_id": doc_id,
                    "chunk_index": idx,
                    "chunk_count": len(chunks),
                    # The chunk's own text is stored so the LLM is grounded on
                    # the passage that actually matched, not the whole document.
                    "chunk_text": chunk,
                }
            )

            points.append(
                PointStruct(
                    id=deterministic_qdrant_id(doc_id, idx),
                    vector=vectors,
                    payload=payload,
                )
            )

        results.append((row, points))

    return results


def _delete_stale_chunks_sync(qdrant, source: KBSource, doc_id: str, keep: int) -> None:
    """
    Remove chunk points beyond the document's current chunk count.

    Needed because a document that shrinks (e.g. a CVE description is edited
    down) would otherwise leave orphaned high-index chunks that still match
    queries and cite content the document no longer contains.
    """
    try:
        qdrant.delete(
            collection_name=source.collection,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(key="doc_id", match=MatchValue(value=doc_id)),
                        FieldCondition(key="chunk_index", range=Range(gte=keep)),
                    ]
                )
            ),
        )
    except Exception as exc:
        log.debug("Stale chunk cleanup for %s/%s: %s", source.key, doc_id, exc)


async def index_rows(
    source: KBSource,
    session: AsyncSession,
    qdrant,
    rows: Sequence[Any],
    *,
    force: bool = False,
    batch_size: int = 128,
) -> IndexStats:
    """
    Index the given rows into the source's collection.

    Rows whose `content_hash` and `embed_model` signature are unchanged are
    skipped unless `force=True` — that is what makes a routine re-sync cheap.

    `batch_size` is rows per encoder pass, not points per upsert: all chunks of
    all rows in a batch are embedded in one call, then written in one upsert.

    Ordering is deliberate per batch:
        build text → skip unchanged → embed → upsert vectors →
        stamp qdrant_synced_at → commit
    so a failure at any step never leaves a row claiming to be synced when it
    is not.  The reverse order is what produced the current split-brain state.
    """
    stats = IndexStats()
    signature = current_embed_signature()
    await ensure_collection(qdrant, source)

    # Rows staged for the next encoder pass: (row, text, hash, was_indexed).
    staged: list[tuple[Any, str, str, bool]] = []

    async def flush() -> None:
        nonlocal staged
        if not staged:
            return
        pending = staged
        staged = []

        try:
            built = await asyncio.to_thread(
                _build_points_batch, source, [(row, text) for row, text, _, _ in pending]
            )
        except Exception as exc:
            log.error("Embedding failed for %s (%d rows): %s", source.key, len(pending), exc)
            stats.failed += len(pending)
            return

        points: list[PointStruct] = []
        for _, row_points in built:
            points.extend(row_points)
        if not points:
            return

        try:
            await asyncio.to_thread(
                qdrant.upsert, collection_name=source.collection, points=points, wait=True
            )
        except Exception as exc:
            log.error("Qdrant upsert failed for %s (%d points): %s", source.key, len(points), exc)
            stats.failed += len(pending)
            return

        now = datetime.utcnow()
        for (row, _, chash, was_indexed), (_, row_points) in zip(pending, built):
            if not row_points:
                continue
            # Only a document that was already indexed can have leftover chunks
            # from a previous, longer version. Probing every row on a first
            # index would add one HTTP round-trip per document — the dominant
            # cost of a large backfill — to delete nothing.
            if was_indexed:
                await asyncio.to_thread(
                    _delete_stale_chunks_sync,
                    qdrant,
                    source,
                    _doc_id_of(source, row),
                    len(row_points),
                )
            row.content_hash = chash
            row.embed_model = signature
            row.qdrant_synced_at = now
            stats.indexed += 1
            stats.chunks += len(row_points)

        await session.commit()

    for row in rows:
        stats.considered += 1
        try:
            text = source.build_text(row)
            if not text.strip():
                continue

            chash = content_hash(text)
            was_indexed = row.qdrant_synced_at is not None
            unchanged = (
                not force
                and row.content_hash == chash
                and row.embed_model == signature
                and was_indexed
            )
            if unchanged:
                stats.skipped_unchanged += 1
                continue

            staged.append((row, text, chash, was_indexed))
            if len(staged) >= batch_size:
                await flush()
        except Exception as exc:
            stats.failed += 1
            log.warning(
                "Indexing failed for %s/%s: %s",
                source.key,
                _doc_id_of(source, row),
                exc,
            )

    await flush()
    invalidate_health_cache()
    log.info("Indexed %s: %s", source.key, stats)
    return stats


async def reindex_source(
    source: KBSource,
    session: AsyncSession,
    qdrant,
    *,
    force: bool = False,
    chunk_rows: int = 500,
    batch_size: int = 128,
) -> IndexStats:
    """
    Re-index an entire source, streaming rows in windows so a large corpus
    never has to fit in memory.
    """
    from sqlalchemy import select

    total = IndexStats()
    offset = 0
    while True:
        stmt = (
            select(source.model)
            .order_by(source.pk_column)
            .offset(offset)
            .limit(chunk_rows)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            break

        s = await index_rows(
            source, session, qdrant, rows, force=force, batch_size=batch_size
        )
        total.considered += s.considered
        total.indexed += s.indexed
        total.skipped_unchanged += s.skipped_unchanged
        total.chunks += s.chunks
        total.failed += s.failed

        offset += len(rows)

    return total


async def delete_document(qdrant, source: KBSource, doc_id: str) -> None:
    """Remove every chunk of a document from its collection."""
    await asyncio.to_thread(
        qdrant.delete,
        collection_name=source.collection,
        points_selector=FilterSelector(
            filter=Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
            )
        ),
    )
    invalidate_health_cache()
