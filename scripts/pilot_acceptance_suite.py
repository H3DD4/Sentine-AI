"""Run a broad, isolated retrieval acceptance suite before production migration.

This script only recreates ``pilot_*`` Qdrant collections. It never changes
PostgreSQL sync fields or production collections. Every case has a stable label,
an expected document id, and a check on the returned document identity.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass

from qdrant_client.models import FusionQuery, Prefetch, SparseVector as QdrantSparseVector
from sqlalchemy import or_, select

from app.db import AsyncSessionLocal
from app.ingestion.embedder import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    chunk_config,
    current_embed_signature,
    embedding_dim,
    get_model,
    load_model_sync,
    load_sparse_model_sync,
    embed_query,
    sparse_embed_query,
)
from app.kb.indexer import _build_points_batch, ensure_collection_sync
from app.kb.registry import get_source
from app.services.retrieval import get_qdrant, init_qdrant_client


class PilotSource:
    def __init__(self, source):
        self._source = source
        self.key = source.key
        self.collection = f"pilot_{source.collection}_acceptance"
        self.model = source.model
        self.pk_column = source.pk_column

    def __getattr__(self, name):
        return getattr(self._source, name)


@dataclass(frozen=True)
class Case:
    label: str
    source: str
    query: str
    expected_doc_id: str
    expected_text: str
    exact: bool = False


def _id(source, row) -> str:
    return str(getattr(row, source.pk_column.key))


def _cases(source_key: str, rows: list, source) -> list[Case]:
    cases: list[Case] = []
    for index, row in enumerate(rows[:4]):
        doc_id = _id(source, row)
        if source_key == "nvd":
            query = f"What is {doc_id}?"
            text = doc_id
        elif source_key == "mitre":
            query = doc_id
            text = doc_id
        elif source_key == "owasp":
            query = doc_id
            text = doc_id
        elif source_key == "owasp_docs":
            query = row.title
            text = row.title
        else:
            query = str(row.template_code)
            text = str(row.template_code)
        cases.append(Case(f"{source_key}.exact.{index + 1}", source_key, query, doc_id, text, True))

    if rows:
        row = rows[0]
        doc_id = _id(source, row)
        if source_key == "nvd":
            words = (row.description or "").split()
            query = " ".join(words[: min(10, len(words))]) or doc_id
            text = words[0] if words else doc_id
        elif source_key == "mitre":
            query, text = f"how to detect {row.name}", row.name
        elif source_key == "owasp":
            query, text = f"prevent {row.name}", row.name
        elif source_key == "owasp_docs":
            row = next(
                (candidate for candidate in rows if "server side request forgery" in candidate.title.lower()),
                row,
            )
            doc_id = _id(source, row)
            query, text = row.title, row.title
        else:
            query, text = f"recommendation to remediate {row.title}", row.title
        cases.append(Case(f"{source_key}.semantic.1", source_key, query, doc_id, text))
    return cases


async def _query(qdrant, collection: str, query: str, limit: int = 10) -> list[dict]:
    dense = await asyncio.to_thread(embed_query, query)
    sparse = await asyncio.to_thread(sparse_embed_query, query)
    prefetch = [Prefetch(query=dense, using=DENSE_VECTOR_NAME, limit=40)]
    if sparse is not None and not sparse.is_empty():
        prefetch.append(Prefetch(
            query=QdrantSparseVector(indices=sparse.indices, values=sparse.values),
            using=SPARSE_VECTOR_NAME,
            limit=40,
        ))
    result = await asyncio.to_thread(
        qdrant.query_points,
        collection_name=collection,
        prefetch=prefetch,
        query=FusionQuery(fusion="rrf"),
        limit=limit,
        with_payload=True,
    )
    unique: list[dict] = []
    seen: set[str] = set()
    for rank, point in enumerate(result.points, 1):
        payload = point.payload or {}
        doc_id = str(payload.get("doc_id") or "")
        if doc_id in seen:
            continue
        seen.add(doc_id)
        unique.append({
            "rank": len(unique) + 1,
            "doc_id": doc_id,
            "score": point.score,
            "text": payload.get("chunk_text") or "",
            "has_identity": f"Document ID: {doc_id}" in (payload.get("chunk_text") or ""),
        })
    return unique


async def main(limit: int) -> None:
    load_model_sync()
    load_sparse_model_sync()
    init_qdrant_client()
    qdrant = get_qdrant()
    tokenizer = get_model().tokenizer
    chunk_limit, _ = chunk_config()
    source_keys = ["nvd", "mitre", "owasp", "owasp_docs", "finding_templates"]
    all_cases: list[Case] = []
    reports: list[dict] = []

    async with AsyncSessionLocal() as session:
        for source_key in source_keys:
            source = get_source(source_key)
            pilot = PilotSource(source)
            if await asyncio.to_thread(qdrant.collection_exists, pilot.collection):
                await asyncio.to_thread(qdrant.delete_collection, pilot.collection)
            ensure_collection_sync(qdrant, pilot, dim=embedding_dim())

            if source_key == "owasp_docs":
                stmt = select(source.model).where(or_(
                    source.model.title.ilike("%server side request forgery%"),
                    source.model.title.ilike("%JSON Web Token%"),
                    source.model.title.ilike("%JWT%"),
                )).order_by(source.model.title).limit(limit)
            else:
                stmt = select(source.model).order_by(source.pk_column).limit(limit)
            rows = list((await session.execute(stmt)).scalars().all())
            built = await asyncio.to_thread(
                _build_points_batch, pilot, [(row, source.build_text(row)) for row in rows]
            )
            points = [point for _, row_points in built for point in row_points]
            if points:
                await asyncio.to_thread(qdrant.upsert, collection_name=pilot.collection, points=points, wait=True)

            lengths = [len(tokenizer.encode((p.payload or {}).get("chunk_text", ""), add_special_tokens=False)) for p in points]
            ids = [p.id for p in points]
            headers = [
                f"Document ID: {(p.payload or {}).get('doc_id')}" in (p.payload or {}).get("chunk_text", "")
                for p in points
            ]
            report = {
                "source": source_key,
                "rows": len(rows),
                "points": len(points),
                "max_chunk_tokens": max(lengths, default=0),
                "all_chunks_within_limit": max(lengths, default=0) <= chunk_limit,
                "all_chunks_self_describing": all(headers),
                "unique_point_ids": len(ids) == len(set(ids)),
            }
            reports.append(report)
            if not all(report[key] for key in ("all_chunks_within_limit", "all_chunks_self_describing", "unique_point_ids")):
                raise AssertionError(f"Index contract failed: {report}")
            all_cases.extend(_cases(source_key, rows, source))

    if len(all_cases) < 20:
        raise AssertionError(f"Only {len(all_cases)} cases were generated; expected at least 20")

    case_results = []
    for case in all_cases:
        collection = f"pilot_{get_source(case.source).collection}_acceptance"
        runs = [await _query(qdrant, collection, case.query) for _ in range(3)]
        first = runs[0]
        expected_hits = [h for h in first if h["doc_id"] == case.expected_doc_id]
        top_rank = expected_hits[0]["rank"] if expected_hits else None
        run_ranks = [
            next((h["rank"] for h in run if h["doc_id"] == case.expected_doc_id), None)
            for run in runs
        ]
        rank_requirement = top_rank == 1 if case.exact else top_rank is not None and top_rank <= 5
        repeated_rank_requirement = all(
            rank == 1 if case.exact else rank is not None and rank <= 5
            for rank in run_ranks
        )
        expected_content = bool(expected_hits) and case.expected_text.lower() in expected_hits[0]["text"].lower()
        result = {
            "label": case.label,
            "query": case.query,
            "expected_doc_id": case.expected_doc_id,
            "expected_text": case.expected_text,
            "exact": case.exact,
            "top_rank": top_rank,
            "run_ranks": run_ranks,
            "rank_requirement_met": rank_requirement,
            "rank_requirement_met_all_runs": repeated_rank_requirement,
            "expected_content_present": expected_content,
            "all_identity_headers": all(h["has_identity"] for h in first),
            "hits": first[:5],
        }
        case_results.append(result)
        if not all(result[key] for key in (
            "rank_requirement_met",
            "rank_requirement_met_all_runs",
            "expected_content_present",
            "all_identity_headers",
        )):
            raise AssertionError(f"Retrieval acceptance failed: {result}")

    output = {
        "signature": current_embed_signature(),
        "chunk_config": chunk_config(),
        "case_count": len(case_results),
        "passed_cases": sum(1 for r in case_results if r["rank_requirement_met"]),
        "index_reports": reports,
        "cases": case_results,
        "production_migration": "BLOCKED: pilot evidence only; no production changes made",
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    asyncio.run(main(max(4, args.limit)))
