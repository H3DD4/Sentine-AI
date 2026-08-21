"""Export the public retrieval corpus and ready-to-query Qdrant points.

The export deliberately contains only knowledge-base sources that are safe to
ship with the application. User accounts, findings, engagements, reports,
conversations, audit records, Ghostwriter data, and internal analyst notes are
never included.

Run against the populated local services before packaging a release:

    python -m scripts.export_retrieval_seed --output seed/retrieval
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.inspection import inspect

from app.db import AsyncSessionLocal
from app.ingestion.embedder import current_embed_signature
from app.kb.registry import get_source
from app.services.retrieval import get_qdrant, init_qdrant_client

SAFE_SOURCES = ("nvd", "mitre", "owasp", "owasp_docs", "finding_templates")


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Unsupported value: {type(value).__name__}")


def _write_gz(path: Path, records) -> str:
    digest = hashlib.sha256()
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as stream:
        for record in records:
            line = json.dumps(record, default=_json_default, ensure_ascii=False, separators=(",", ":")) + "\n"
            stream.write(line)
            digest.update(line.encode("utf-8"))
    return digest.hexdigest()


async def main(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for artifact in output.glob("*.jsonl.gz"):
        artifact.unlink()
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()
    init_qdrant_client()
    qdrant = get_qdrant()
    manifest = {
        "format": 1,
        "embedding_signature": current_embed_signature(),
        "sources": {},
        "warning": "Seed excludes users, client data, Ghostwriter, internal notes, and application records.",
    }

    async with AsyncSessionLocal() as session:
        for key in SAFE_SOURCES:
            source = get_source(key)
            rows = (await session.execute(select(source.model).order_by(source.pk_column))).scalars().all()
            signature_rows = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(source.model)
                        .where(source.model.embed_model == manifest["embedding_signature"])
                    )
                ).scalar_one()
            )
            if signature_rows != len(rows):
                raise RuntimeError(
                    f"Refusing to export {key}: {signature_rows}/{len(rows)} rows use the current signature"
                )
            row_records = []
            for row in rows:
                row_records.append({column.key: getattr(row, column.key) for column in inspect(source.model).columns})
            rows_path = output / f"{key}.rows.jsonl.gz"
            row_hash = _write_gz(rows_path, row_records)

            points = []
            if qdrant.collection_exists(source.collection):
                offset = None
                while True:
                    page, offset = qdrant.scroll(
                        source.collection, offset=offset, limit=256, with_payload=True, with_vectors=True
                    )
                    points.extend(record.model_dump(mode="json") for record in page)
                    if offset is None:
                        break
            points_path = output / f"{key}.points.jsonl.gz"
            point_hash = _write_gz(points_path, points)
            if rows and not points:
                raise RuntimeError(f"Refusing to export {key}: rows exist but its Qdrant collection is empty")
            manifest["sources"][key] = {
                "table": source.table,
                "collection": source.collection,
                "rows": len(row_records),
                "points": len(points),
                "rows_sha256": row_hash,
                "points_sha256": point_hash,
            }

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("seed/retrieval"))
    args = parser.parse_args()
    asyncio.run(main(args.output))
