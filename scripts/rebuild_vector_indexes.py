"""Recreate Qdrant collections and re-embed existing PostgreSQL KB rows.

Use this after changing the dense embedding model or vector dimension. The
operation is source-by-source and resumable: PostgreSQL remains authoritative,
and a failed source can be rerun independently with ``--source``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import func, or_, select, update

from app.db import AsyncSessionLocal
from app.ingestion.embedder import (
    current_embed_signature,
    embedding_dim,
    load_model_sync,
    load_sparse_model_sync,
)
from app.kb.indexer import IndexStats, index_rows, rebuild_collection, reindex_source
from app.kb.registry import all_sources, get_source, source_keys
from app.services.retrieval import get_qdrant, init_qdrant_client


async def rebuild(source_key: str, *, recreate: bool = False, batch_size: int = 8) -> None:
    # Load before deleting any collection. A missing or incompatible model must
    # fail while the old indexes are still intact.
    load_model_sync()
    load_sparse_model_sync()
    dim = embedding_dim()
    signature = current_embed_signature()
    if dim <= 0:
        raise RuntimeError(f"Invalid embedding dimension: {dim}")

    init_qdrant_client()
    qdrant = get_qdrant()
    selected = all_sources() if source_key == "all" else [get_source(source_key)]

    async with AsyncSessionLocal() as session:
        for source in selected:
            rows = await source.row_count(session)
            unsynced = await source.unsynced_count(session)
            current_signature_rows = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(source.model)
                        .where(source.model.embed_model == signature)
                    )
                ).scalar_one()
            )
            collection_info = None
            if await asyncio.to_thread(qdrant.collection_exists, source.collection):
                collection_info = await asyncio.to_thread(
                    qdrant.get_collection, source.collection
                )
            vectors = 0
            vector_config = (
                getattr(collection_info.config.params, "vectors", None)
                if collection_info
                else None
            )
            if collection_info is not None:
                vectors = int(
                    (await asyncio.to_thread(qdrant.count, source.collection, exact=True)).count
                )
            if isinstance(vector_config, dict):
                dense_config = vector_config.get("dense")
            else:
                dense_config = vector_config
            configured_dim = getattr(dense_config, "size", None) if dense_config else None
            ready = (
                collection_info is not None
                and configured_dim == dim
                and unsynced == 0
                and current_signature_rows == rows
                and (rows == 0 or vectors > 0)
            )
            if ready:
                print(
                    f"\nSkipping {source.key}: already ready "
                    f"(rows={rows}, vectors={vectors}, dimension={configured_dim})"
                )
                continue

            if collection_info is not None and configured_dim == dim and not recreate:
                print(
                    f"\nResuming {source.key}: rows={rows}, unsynced={unsynced}, "
                    f"current_signature_rows={current_signature_rows}, vectors={vectors}, "
                    f"dimension={configured_dim}"
                )
                stats = IndexStats()
                while True:
                    pending = list(
                        (
                            await session.execute(
                                select(source.model)
                                .where(
                                    or_(
                                        source.model.qdrant_synced_at.is_(None),
                                        source.model.embed_model.is_distinct_from(signature),
                                    )
                                )
                                .order_by(source.pk_column)
                                .limit(500)
                            )
                        ).scalars().all()
                    )
                    if not pending:
                        break
                    batch = await index_rows(
                        source, session, qdrant, pending, force=False, batch_size=batch_size
                    )
                    stats.considered += batch.considered
                    stats.indexed += batch.indexed
                    stats.skipped_unchanged += batch.skipped_unchanged
                    stats.chunks += batch.chunks
                    stats.failed += batch.failed
                    if batch.failed:
                        break
                print(f"  {source.key}: {stats.to_dict()}")
                if stats.failed:
                    raise RuntimeError(
                        f"{source.key} failed to index {stats.failed} rows; rerun with "
                        f"--source {source.key}"
                    )
                continue

            print(
                f"\nRebuilding {source.key}: rows={rows}, unsynced={unsynced}, "
                f"current_signature_rows={current_signature_rows}, "
                f"vectors={vectors}, dimension={configured_dim} -> {dim}"
            )
            await rebuild_collection(qdrant, source, dim=dim)
            if not rows:
                print(f"  {source.key}: empty collection ready")
                continue

            # A rebuilt collection contains no confirmed vectors. Mark every
            # row unsynced before indexing so health reports partial coverage if
            # the process is interrupted.
            await session.execute(
                update(source.model).values(
                    qdrant_synced_at=None,
                    embed_model=None,
                )
            )
            await session.commit()

            stats = await reindex_source(
                source, session, qdrant, force=True, batch_size=batch_size
            )
            print(f"  {source.key}: {stats.to_dict()}")
            if stats.failed:
                raise RuntimeError(
                    f"{source.key} failed to index {stats.failed} rows; rerun with "
                    f"--source {source.key}"
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("all", *source_keys()), default="all")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Recreate selected collections before reindexing (for index text format changes).",
    )
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(rebuild(args.source, recreate=args.recreate, batch_size=max(1, args.batch_size)))
