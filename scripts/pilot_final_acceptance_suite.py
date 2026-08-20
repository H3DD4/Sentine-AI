"""Thirty harder isolated acceptance tests for retrieval and chunk integrity.

The suite recreates only ``pilot_*_final`` Qdrant collections. It does not alter
production collections or PostgreSQL synchronization fields. A failure stops
before any production migration is permitted.
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
    chunk_text_with_header,
    current_embed_signature,
    embedding_dim,
    embed_query,
    get_model,
    load_model_sync,
    load_sparse_model_sync,
    sparse_embed_query,
)
from app.kb.indexer import _build_points_batch, _chunk_header, ensure_collection_sync
from app.kb.registry import get_source
from app.services.retrieval import get_qdrant, init_qdrant_client


class PilotSource:
    def __init__(self, source):
        self._source = source
        self.key = source.key
        self.collection = f"pilot_{source.collection}_final"
        self.model = source.model
        self.pk_column = source.pk_column

    def __getattr__(self, name):
        return getattr(self._source, name)


@dataclass(frozen=True)
class HardCase:
    label: str
    source: str
    query: str
    expected_id: str
    expected_text: str
    exact: bool = False


def _doc_id(source, row) -> str:
    return str(getattr(row, source.pk_column.key))


def _body_words(source, row) -> str:
    text = source.build_text(row).split()
    return " ".join(text[: min(12, len(text))])


def _hard_cases(source_key: str, rows: list, source) -> list[HardCase]:
    cases: list[HardCase] = []
    for index, row in enumerate(rows[:6]):
        doc_id = _doc_id(source, row)
        title = str(getattr(row, "title", "") or getattr(row, "name", "") or doc_id)
        if source_key == "nvd":
            title = str(getattr(row, "description", "") or doc_id).split(".")[0]
            metadata = " ".join(filter(None, [str(getattr(row, "severity", "")), str(getattr(row, "cwe", ""))]))
        elif source_key == "mitre":
            metadata = " ".join(str(x) for x in (getattr(row, "tactics", []) or [])[:2])
        elif source_key == "owasp":
            metadata = str(getattr(row, "year", ""))
        elif source_key == "owasp_docs":
            metadata = " ".join(str(x) for x in (getattr(row, "cwe_ids", []) or [])[:2])
        else:
            metadata = str(getattr(row, "category", "") or getattr(row, "topic", ""))

        cases.extend([
            HardCase(f"{source_key}.hard.{index + 1}.exact-noise", source_key, f"please locate [{doc_id}] now", doc_id, doc_id, True),
            HardCase(f"{source_key}.hard.{index + 1}.case-variant", source_key, doc_id.lower().replace("-", " "), doc_id, doc_id, True),
            HardCase(f"{source_key}.hard.{index + 1}.title-context", source_key, f"security documentation about {title}", doc_id, doc_id),
            HardCase(f"{source_key}.hard.{index + 1}.body-fragment", source_key, _body_words(source, row), doc_id, doc_id),
            HardCase(f"{source_key}.hard.{index + 1}.metadata", source_key, f"{metadata} {title}".strip(), doc_id, doc_id),
        ])
        if len(cases) >= 6:
            break
    # Six cases per source keeps the final suite exactly 30 and comparable.
    return cases[:6]


async def _hybrid_query(qdrant, collection: str, text: str, limit: int = 10) -> list[dict]:
    dense = await asyncio.to_thread(embed_query, text)
    sparse = await asyncio.to_thread(sparse_embed_query, text)
    prefetch = [Prefetch(query=dense, using=DENSE_VECTOR_NAME, limit=40)]
    if sparse is not None and not sparse.is_empty():
        prefetch.append(Prefetch(
            query=QdrantSparseVector(indices=sparse.indices, values=sparse.values),
            using=SPARSE_VECTOR_NAME,
            limit=40,
        ))
    response = await asyncio.to_thread(
        qdrant.query_points,
        collection_name=collection,
        prefetch=prefetch,
        query=FusionQuery(fusion="rrf"),
        limit=limit,
        with_payload=True,
    )
    hits: list[dict] = []
    seen: set[str] = set()
    for point in response.points:
        payload = point.payload or {}
        doc_id = str(payload.get("doc_id") or "")
        if doc_id in seen:
            continue
        seen.add(doc_id)
        chunk = str(payload.get("chunk_text") or "")
        hits.append({
            "rank": len(hits) + 1,
            "doc_id": doc_id,
            "score": point.score,
            "text": chunk,
            "identity": f"Document ID: {doc_id}" in chunk,
        })
    return hits


def _assert_chunk_edges(tokenizer, limit: int) -> dict:
    header = _chunk_header("EDGE-DOCUMENT-42", {
        "title": "Long token-dense boundary record",
        "severity": "CRITICAL",
        "cvss_v3": 9.8,
        "cwe": "CWE-918",
    })
    body = ("CPE cpe:2.3:a:vendor:product:1.2.3 "
            "base64 QWxhZGRpbjpvcGVuIHNlc2FtZQ== "
            "stacktrace NullPointerException ") * 2200
    chunks = chunk_text_with_header(header, body)
    lengths = [len(tokenizer.encode(chunk, add_special_tokens=False)) for chunk in chunks]
    return {
        "chunk_count": len(chunks),
        "max_tokens": max(lengths, default=0),
        "within_limit": bool(chunks) and max(lengths) <= limit,
        "identity_on_every_chunk": all("Document ID: EDGE-DOCUMENT-42" in chunk for chunk in chunks),
        "metadata_on_every_chunk": all("CWE-918" in chunk and "CVSS: 9.8" in chunk for chunk in chunks),
        "first_and_last_body_present": bool(chunks) and "CPE cpe:2.3" in chunks[0] and "stacktrace" in chunks[-1],
    }


async def main(limit: int) -> None:
    load_model_sync()
    load_sparse_model_sync()
    init_qdrant_client()
    qdrant = get_qdrant()
    tokenizer = get_model().tokenizer
    chunk_limit, overlap = chunk_config()
    edge_report = _assert_chunk_edges(tokenizer, chunk_limit)
    if not all(edge_report.values()):
        raise AssertionError(f"Chunk edge test failed: {edge_report}")

    source_keys = ["nvd", "mitre", "owasp", "owasp_docs", "finding_templates"]
    cases: list[HardCase] = []
    index_reports = []
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
                )).order_by(source.model.title).limit(max(6, limit))
            else:
                stmt = select(source.model).order_by(source.pk_column).limit(max(6, limit))
            rows = list((await session.execute(stmt)).scalars().all())
            built = await asyncio.to_thread(
                __import__("app.kb.indexer", fromlist=["_build_points_batch"])._build_points_batch,
                pilot,
                [(row, source.build_text(row)) for row in rows],
            )
            points = [point for _, row_points in built for point in row_points]
            await asyncio.to_thread(qdrant.upsert, collection_name=pilot.collection, points=points, wait=True)
            lengths = [len(tokenizer.encode((p.payload or {}).get("chunk_text", ""), add_special_tokens=False)) for p in points]
            index_report = {
                "source": source_key,
                "rows": len(rows),
                "points": len(points),
                "max_chunk_tokens": max(lengths, default=0),
                "within_limit": max(lengths, default=0) <= chunk_limit,
                "all_self_describing": all(
                    f"Document ID: {(p.payload or {}).get('doc_id')}" in (p.payload or {}).get("chunk_text", "")
                    for p in points
                ),
                "unique_point_ids": len({p.id for p in points}) == len(points),
            }
            index_reports.append(index_report)
            if not all(index_report[key] for key in ("within_limit", "all_self_describing", "unique_point_ids")):
                raise AssertionError(f"Index edge test failed: {index_report}")
            cases.extend(_hard_cases(source_key, rows, source))

    if len(cases) != 30:
        raise AssertionError(f"Expected exactly 30 cases, generated {len(cases)}")

    results = []
    for case in cases:
        collection = f"pilot_{get_source(case.source).collection}_final"
        runs = [await _hybrid_query(qdrant, collection, case.query) for _ in range(3)]
        ranks = [next((h["rank"] for h in run if h["doc_id"] == case.expected_id), None) for run in runs]
        target = next((h for h in runs[0] if h["doc_id"] == case.expected_id), None)
        result = {
            "label": case.label,
            "query": case.query,
            "expected_id": case.expected_id,
            "exact": case.exact,
            "ranks": ranks,
            "target_content": bool(target and case.expected_text.lower() in target["text"].lower()),
            "target_rank_valid_all_runs": all(
                rank == 1 if case.exact else rank is not None and rank <= 5 for rank in ranks
            ),
            "identity_headers": all(h["identity"] for h in runs[0]),
            "hits": runs[0][:5],
        }
        results.append(result)
        if not all(result[key] for key in ("target_content", "target_rank_valid_all_runs", "identity_headers")):
            raise AssertionError(f"Final acceptance failure: {result}")

    print(json.dumps({
        "signature": current_embed_signature(),
        "chunk_config": {"tokens": chunk_limit, "overlap": overlap},
        "chunk_edge_report": edge_report,
        "index_reports": index_reports,
        "case_count": len(results),
        "passed_cases": len(results),
        "cases": results,
        "production_migration": "BLOCKED: final pilot evidence only; no production changes made",
    }, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=6)
    args = parser.parse_args()
    asyncio.run(main(max(6, args.limit)))
