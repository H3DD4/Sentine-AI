"""Restore the versioned retrieval seed without scraping or re-embedding."""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy import func, select
from sqlalchemy.types import DateTime as SQLDateTime

from app.db import AsyncSessionLocal
from app.ingestion.embedder import current_embed_signature
from app.kb.registry import get_source
from app.services.retrieval import get_qdrant, init_qdrant_client
from app.kb.indexer import ensure_collection_sync
from qdrant_client.models import PointStruct

SAFE_SOURCES = ("nvd", "mitre", "owasp", "owasp_docs", "finding_templates")


def _records(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def _verify_file(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise RuntimeError(f"Seed checksum mismatch: {path}")


def _restore_dates(model, record: dict) -> dict:
    for column in model.__table__.columns:
        if isinstance(column.type, SQLDateTime) and isinstance(record.get(column.key), str):
            try:
                record[column.key] = datetime.fromisoformat(record[column.key])
            except ValueError:
                pass
    return record


async def main(seed: Path) -> None:
    manifest = json.loads((seed / "manifest.json").read_text(encoding="utf-8"))
    expected = manifest["embedding_signature"]
    actual = current_embed_signature()
    if expected != actual:
        raise RuntimeError(f"Seed signature {expected!r} does not match application {actual!r}")

    for key in SAFE_SOURCES:
        info = manifest["sources"][key]
        _verify_file(seed / f"{key}.rows.jsonl.gz", info["rows_sha256"])
        _verify_file(seed / f"{key}.points.jsonl.gz", info["points_sha256"])

    init_qdrant_client()
    qdrant = get_qdrant()
    async with AsyncSessionLocal() as session:
        existing = {}
        for key in SAFE_SOURCES:
            source = get_source(key)
            existing[key] = int((await session.execute(select(func.count()).select_from(source.model))).scalar_one())
        rows_match = all(existing[key] == manifest["sources"][key]["rows"] for key in SAFE_SOURCES)
        if any(existing.values()) and not rows_match:
            print(
                "retrieval tables contain data that differs from the bundled seed; "
                "assuming the corpus was updated and leaving both stores unchanged"
            )
            return
        for key in SAFE_SOURCES:
            source = get_source(key)
            info = manifest["sources"][key]
            rows = list(_records(seed / f"{key}.rows.jsonl.gz"))
            if not rows_match:
                await session.execute(delete(source.model))
                for record in rows:
                    session.add(source.model(**_restore_dates(source.model, record)))
                await session.commit()

            points_path = seed / f"{key}.points.jsonl.gz"
            collection_ready = qdrant.collection_exists(source.collection)
            if collection_ready:
                collection_ready = qdrant.count(source.collection, exact=True).count == info["points"]
            if not collection_ready:
                if qdrant.collection_exists(source.collection):
                    qdrant.delete_collection(source.collection)
                ensure_collection_sync(qdrant, source)
                batch = []
                restored_points = 0
                for record in _records(points_path):
                    batch.append(PointStruct.model_validate(record))
                    if len(batch) == 256:
                        qdrant.upsert(collection_name=source.collection, points=batch, wait=True)
                        restored_points += len(batch)
                        batch = []
                if batch:
                    qdrant.upsert(collection_name=source.collection, points=batch, wait=True)
                    restored_points += len(batch)
                if restored_points != info["points"]:
                    raise RuntimeError(
                        f"Restored {restored_points} {key} points, expected {info['points']}"
                    )
            print(
                f"ready {key}: {len(rows)} rows, {info['points']} points"
                + (" (already present)" if rows_match and collection_ready else "")
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, default=Path("seed/retrieval"))
    args = parser.parse_args()
    asyncio.run(main(args.seed))
