"""Query an isolated pilot collection directly; production is untouched."""

from __future__ import annotations

import argparse
import asyncio
import json

from app.ingestion.embedder import embed_query, sparse_embed_query
from app.services.retrieval import get_qdrant, init_qdrant_client
from qdrant_client.models import FusionQuery, Prefetch, SparseVector as QdrantSparseVector


CASES = {
    "owasp_docs": [
        ("testing for server side request forgery", "Server Side Request Forgery"),
        ("JWT algorithm confusion and weak signature validation", "JWT"),
    ],
    "nvd": [
        ("What is CVE-2023-27159?", "CVE-2023-27159"),
    ],
}


async def main(source_key: str, limit: int) -> None:
    init_qdrant_client()
    qdrant = get_qdrant()
    collection = f"pilot_kb_{source_key}"
    if not await asyncio.to_thread(qdrant.collection_exists, collection):
        raise SystemExit(f"Missing pilot collection: {collection}. Run pilot_index_test first.")

    output = []
    for query, expected_text in CASES.get(source_key, []):
        dense = await asyncio.to_thread(embed_query, query)
        sparse = await asyncio.to_thread(sparse_embed_query, query)
        prefetch = [Prefetch(query=dense, using="dense", limit=40)]
        if sparse is not None and not sparse.is_empty():
            prefetch.append(Prefetch(
                query=QdrantSparseVector(indices=sparse.indices, values=sparse.values),
                using="sparse", limit=40,
            ))
        points = await asyncio.to_thread(
            qdrant.query_points,
            collection_name=collection,
            prefetch=prefetch,
            query=FusionQuery(fusion="rrf"),
            limit=limit,
            with_payload=True,
        )
        hits = [
            {
                "rank": i + 1,
                "doc_id": (point.payload or {}).get("doc_id"),
                "score": point.score,
                "has_identity": f"Document ID: {(point.payload or {}).get('doc_id')}" in (point.payload or {}).get("chunk_text", ""),
                "text": (point.payload or {}).get("chunk_text", "")[:300],
            }
            for i, point in enumerate(points.points)
        ]
        # A document, not every chunk, is the retrieval unit under test.
        unique_hits = []
        seen_docs = set()
        for hit in hits:
            if hit["doc_id"] in seen_docs:
                continue
            seen_docs.add(hit["doc_id"])
            unique_hits.append(hit)
        output.append({
            "query": query,
            "expected_text": expected_text,
            "match_in_top_k": any(expected_text.lower() in h["text"].lower() for h in unique_hits),
            "all_identity_headers": all(h["has_identity"] for h in hits),
            "hits": unique_hits,
        })
    print(json.dumps({"collection": collection, "results": output}, indent=2, default=str))
    if not all(item["all_identity_headers"] for item in output):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(main(args.source, args.limit))
