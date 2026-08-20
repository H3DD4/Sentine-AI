"""Build and test a small isolated pilot index without changing production.

Examples:
    .venv\Scripts\python.exe -m scripts.pilot_index_test --source owasp_docs --limit 10
    .venv\Scripts\python.exe -m scripts.pilot_index_test --source nvd --limit 25

The pilot collection is named ``pilot_<source collection>`` and is deleted and
recreated on each run. PostgreSQL sync fields and production Qdrant collections
are never modified.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from qdrant_client.models import Distance, SparseVectorParams, VectorParams
from sqlalchemy import or_, select

from app.db import AsyncSessionLocal
from app.ingestion.embedder import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    chunk_config,
    current_embed_signature,
    embedding_dim,
    load_model_sync,
    load_sparse_model_sync,
)
from app.kb.indexer import _build_points_batch, ensure_collection_sync
from app.kb.registry import get_source
from app.services.retrieval import get_qdrant, init_qdrant_client


class PilotSource:
    """Delegate source serialization while redirecting only the collection."""

    def __init__(self, source):
        self._source = source
        self.key = source.key
        self.collection = f"pilot_{source.collection}"
        self.model = source.model
        self.pk_column = source.pk_column

    def __getattr__(self, name):
        return getattr(self._source, name)


async def main(source_key: str, limit: int) -> None:
    load_model_sync()
    load_sparse_model_sync()
    source = get_source(source_key)
    pilot = PilotSource(source)
    init_qdrant_client()
    qdrant = get_qdrant()

    if await asyncio.to_thread(qdrant.collection_exists, pilot.collection):
        await asyncio.to_thread(qdrant.delete_collection, pilot.collection)
    ensure_collection_sync(qdrant, pilot, dim=embedding_dim())

    async with AsyncSessionLocal() as session:
        stmt = select(source.model).order_by(source.pk_column).limit(limit)
        expected_ids: list[str] = []
        if source_key == "nvd":
            expected_ids = ["CVE-2023-27159"]
            stmt = select(source.model).where(source.pk_column.in_(expected_ids))
        elif source_key == "owasp_docs":
            stmt = select(source.model).where(or_(
                source.model.title.ilike("%server side request forgery%"),
                source.model.title.ilike("%JSON Web Token%"),
                source.model.title.ilike("%JWT%"),
            )).order_by(source.model.title).limit(min(limit, 3))
        rows = list((await session.execute(stmt)).scalars().all())

    built = await asyncio.to_thread(
        _build_points_batch,
        pilot,
        [(row, source.build_text(row)) for row in rows],
    )
    points = [point for _, row_points in built for point in row_points]
    if points:
        await asyncio.to_thread(
            qdrant.upsert, collection_name=pilot.collection, points=points, wait=True
        )

    tokenizer = getattr(__import__("app.ingestion.embedder", fromlist=["get_model"]), "get_model")().tokenizer
    lengths = [
        len(tokenizer.encode((point.payload or {}).get("chunk_text", ""), add_special_tokens=False))
        for point in points
    ]
    result = {
        "source": source_key,
        "rows_requested": limit,
        "rows_indexed": len(rows),
        "expected_ids": expected_ids,
        "expected_ids_loaded": all(
            expected in {str(getattr(row, source.pk_column.key)) for row in rows}
            for expected in expected_ids
        ),
        "pilot_collection": pilot.collection,
        "points": len(points),
        "signature": current_embed_signature(),
        "chunk_config": chunk_config(),
        "max_chunk_tokens": max(lengths, default=0),
        "all_chunks_within_limit": max(lengths, default=0) <= chunk_config()[0],
        "all_chunks_self_describing": all(
            f"Document ID: {(point.payload or {}).get('doc_id')}" in (point.payload or {}).get("chunk_text", "")
            for point in points
        ),
    }
    print(json.dumps(result, indent=2))
    if (
        not result["all_chunks_within_limit"]
        or not result["all_chunks_self_describing"]
        or not result["expected_ids_loaded"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(main(args.source, args.limit))
