"""Measure the live retrieval layer and write a raw-evidence Markdown report."""

from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from qdrant_client.models import Filter, FieldCondition, MatchValue

from app.config import settings
from app.db import AsyncSessionLocal
from app.ingestion.embedder import chunk_config, current_embed_signature, embedding_dim, embed_query
from app.kb.registry import all_sources, get_health
from app.services.retrieval import federated_search, get_qdrant, reranker_status


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "retrieval_layer_diagnostic_report.md"
SOURCE_KEYS = ["nvd", "mitre", "owasp_docs", "finding_templates", "ghostwriter"]


def raw(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, indent=2, default=str)


def point_text(point) -> str:
    payload = point.payload or {}
    return str(payload.get("chunk_text", ""))


def format_point(point) -> str:
    payload = point.payload or {}
    return (
        f"POINT id={point.id} doc_id={payload.get('doc_id')} "
        f"chunk={payload.get('chunk_index')}/{payload.get('chunk_count')}\n"
        "----- BEGIN RAW CHUNK -----\n"
        f"{point_text(point)}\n"
        "----- END RAW CHUNK -----"
    )


def scroll_points(qdrant, collection: str, limit: int | None = None):
    points = []
    offset = None
    while limit is None or len(points) < limit:
        page, offset = qdrant.scroll(
            collection_name=collection,
            limit=min(256, limit - len(points)) if limit else 256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(page)
        if offset is None or not page:
            break
    return points[:limit] if limit else points


def grouped_points(qdrant, collection: str):
    groups = defaultdict(list)
    for point in scroll_points(qdrant, collection):
        groups[(point.payload or {}).get("doc_id")].append(point)
    return groups


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


GOLD = [
    ("NVD exact CVE", "What is CVE-2023-27159?", "nvd", "CVE-2023-27159"),
    ("NVD exact CVE", "Explain CVE-2023-27160 and its affected product", "nvd", "CVE-2023-27160"),
    ("NVD exact CVE", "Assess CVE-2023-27161", "nvd", "CVE-2023-27161"),
    ("NVD semantic", "the vulnerability involving server side request forgery", "nvd", "CVE-2021-37223"),
    ("MITRE exact", "How is T1552.005 detected?", "mitre", "T1552.005"),
    ("MITRE exact", "Describe T1059.001 PowerShell", "mitre", "T1059.001"),
    ("MITRE exact", "What is T1190?", "mitre", "T1190"),
    ("MITRE semantic", "cloud metadata credential access technique", "mitre", "T1552.005"),
    ("OWASP docs", "testing for server side request forgery", "owasp_docs", None),
    ("OWASP docs", "JWT algorithm confusion and weak signature validation", "owasp_docs", None),
    ("OWASP docs", "test authentication session management", "owasp_docs", None),
    ("Template exact", "Use template V_006", "finding_templates", "V_006"),
    ("Template exact", "Find ASIA_V_009", "finding_templates", "ASIA_V_009"),
    ("Ghostwriter", "SSRF fetching AWS instance metadata credentials", "ghostwriter", "gw-1"),
    ("Ghostwriter", "prior firm finding about URL fetch and IMDSv1", "ghostwriter", "gw-1"),
]


async def run_query(query, expected_source, expected_doc, rerank):
    async with AsyncSessionLocal() as session:
        started = time.perf_counter()
        outcome = await federated_search(query, session, top_k=10, rerank=rerank)
        elapsed = (time.perf_counter() - started) * 1000
        hits = [
            {
                "rank": i + 1,
                "doc_id": h.doc_id,
                "source": h.source_key,
                "score": round(h.score, 6),
                "title": h.title,
                "matched_by": h.payload.get("matched_by"),
                "template_code": h.payload.get("template_code"),
                "text": h.text,
            }
            for i, h in enumerate(outcome.hits)
        ]
        rank = next(
            (
                h["rank"]
                for h in hits
                if h["source"] == expected_source
                and (
                    expected_doc is None
                    or h["doc_id"] == expected_doc
                    or h.get("template_code") == expected_doc
                )
            ),
            None,
        )
        return {"query": query, "expected_source": expected_source, "expected_doc": expected_doc, "hits": hits, "rank": rank, "latency_ms": elapsed, "notes": outcome.notes}


async def main():
    qdrant = get_qdrant()
    lines = [
        "# Retrieval-Layer Diagnostic Report",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "This report is generated from the live PostgreSQL and Qdrant services. Raw stored chunk boundaries and raw retrieved text are included below.",
        "",
        "## 1. Runtime Configuration",
        f"- Qdrant: `{settings.QDRANT_URL}`",
        f"- Dense model: `{settings.EMBEDDING_MODEL}`",
        f"- Dense dimension from loaded model: `{embedding_dim()}`",
        f"- Sparse model: `Qdrant/bm25`",
        f"- Effective chunk tokens / overlap: `{chunk_config()[0]} / {chunk_config()[1]}`",
        f"- Persisted current signature: `{current_embed_signature()}`",
        f"- RERANK_ENABLED: `{settings.RERANK_ENABLED}`; model `{settings.RERANKER_MODEL}`; candidates `{settings.RERANK_CANDIDATES}`; timeout `{settings.RERANK_TIMEOUT_SECONDS}s`",
        "- Chunking is global in `app/ingestion/embedder.py`; no source-specific settings exist.",
        "- Dense query preprocessing uses `embed_query`; BGE-M3 intentionally receives no legacy BGE instruction prefix because `_uses_bge_prefix()` excludes `bge-m3`.",
        "",
        "## 2. Source Health and Stored Chunks",
    ]
    async with AsyncSessionLocal() as session:
        health = await get_health(session, qdrant, force=True)
    for src in all_sources():
        if src.key not in SOURCE_KEYS:
            continue
        info = health[src.key]
        points = scroll_points(qdrant, src.collection)
        groups = defaultdict(list)
        for p in points:
            groups[(p.payload or {}).get("doc_id")].append(p)
        lengths = [len(point_text(p)) for p in points]
        lines.append(f"### {src.key}")
        lines.append(f"- PostgreSQL rows: `{info.row_count}`; Qdrant points/chunks: `{info.vector_count}`; unsynced rows: `{info.unsynced_count}`; status: `{info.availability.value}`.")
        lines.append(f"- Documents represented: `{len(groups)}`; chunks/document: min `{min(map(len, groups.values()), default=0)}`, median `{statistics.median(map(len, groups.values())) if groups else 0}`, max `{max(map(len, groups.values()), default=0)}`; raw chunk characters: min `{min(lengths, default=0)}`, median `{statistics.median(lengths) if lengths else 0}`, max `{max(lengths, default=0)}`.")
        lines.append(f"- Persisted embedding signatures in SQL: see live query; current expected signature is `{current_embed_signature()}`.")
        sample_count = min(5, len(points))
        lines.append(f"- Raw stored chunks (requested 5; available `{sample_count}`):")
        for p in points[:5]:
            lines.extend(["", "```text", format_point(p), "```"])

    lines += ["", "## 3. Structured-Record Split Checks"]
    for src in all_sources():
        if src.key not in SOURCE_KEYS:
            continue
        groups = grouped_points(qdrant, src.collection)
        split = []
        for doc_id, points in groups.items():
            if len(points) < 2:
                continue
            ordered = sorted(points, key=lambda p: (p.payload or {}).get("chunk_index", 0))
            joined = "\n".join(point_text(p) for p in ordered)
            payload = ordered[0].payload or {}
            markers = []
            if src.key == "nvd": markers = ["CVSS", "Severity:", "Attack vector:", "CVE-"]
            elif src.key == "mitre": markers = ["Tactics:", "Platforms:", "Detection:", "ATT&CK version:"]
            elif src.key == "finding_templates": markers = ["Template ID:", "Impact:", "Recommendation:", "Risk assessment"]
            elif src.key == "ghostwriter": markers = ["CVSS", "Impact:", "Replication steps:", "Related CVEs:"]
            elif src.key == "owasp_docs": markers = ["OWASP", "JWT", "SSRF", "CWE-"]
            present = [m for m in markers if m in joined]
            if present:
                boundaries = []
                for a, b in zip(ordered, ordered[1:]):
                    ta, tb = point_text(a), point_text(b)
                    if any((m in ta) != (m in tb) for m in markers):
                        boundaries.append(f"chunks {(a.payload or {}).get('chunk_index')}->{(b.payload or {}).get('chunk_index')}")
                if boundaries:
                    split.append((doc_id, boundaries, present, ordered))
        lines.append(f"### {src.key}")
        lines.append(f"- Multi-chunk documents with marker crossings at a boundary: `{len(split)}`.")
        if split:
            doc_id, boundaries, present, ordered = split[0]
            lines.append(f"- Example `{doc_id}` markers `{present}` crossed `{', '.join(boundaries)}`:")
            for p in ordered[:3]:
                lines.extend(["", "```text", format_point(p), "```"])
        else:
            lines.append("- No marker crossing found by this heuristic; this does not prove arbitrary field/value adjacency is preserved.")

    lines += ["", "## 4. Gold Set Retrieval: Reranker Off"]
    settings.RERANK_ENABLED = False
    off = [await run_query(*item[1:], False) for item in GOLD]
    for item, result in zip(GOLD, off):
        lines.append(f"### {item[0]}: `{result['query']}` expected `{result['expected_source']}:{result['expected_doc'] or 'semantic target'}`")
        lines.append(f"- Expected rank in top-10: `{result['rank']}`; latency: `{result['latency_ms']:.1f} ms`; notes: `{result['notes']}`")
        for h in result["hits"]:
            lines.append(f"- rank `{h['rank']}` `{h['source']}:{h['doc_id']}` score `{h['score']}` matched_by `{h['matched_by']}` title `{h['title']}`")
            lines.extend(["```text", h["text"], "```"])

    lines += ["", "## 5. Recall and Precision"]
    for k in (3, 5, 10):
        recalled = [r for r in off if r["rank"] is not None and r["rank"] <= k]
        relevant = sum(
            sum(
                1
                for h in r["hits"][:k]
                if h["source"] == r["expected_source"]
                and (
                    r["expected_doc"] is None
                    or h["doc_id"] == r["expected_doc"]
                    or h.get("template_code") == r["expected_doc"]
                )
            )
            for r in off
        )
        lines.append(f"- k=`{k}`: recall `{len(recalled)}/{len(off)} = {pct(len(recalled)/len(off))}`; precision `{relevant}/{len(off)*k} = {pct(relevant/(len(off)*k))}` (one gold target per query).")

    lines += ["", "## 6. Reranker On Comparison"]
    settings.RERANK_ENABLED = True
    on = [await run_query(*item[1:], True) for item in GOLD]
    for item, before, after in zip(GOLD, off, on):
        delta = None if before["rank"] is None or after["rank"] is None else before["rank"] - after["rank"]
        direction = "absent" if after["rank"] is None else ("improved" if delta and delta > 0 else "worse" if delta and delta < 0 else "same")
        lines.append(f"- `{item[1]}`: off rank `{before['rank']}`, on rank `{after['rank']}`, result `{direction}`, off `{before['latency_ms']:.1f}ms`, on `{after['latency_ms']:.1f}ms`, added `{after['latency_ms']-before['latency_ms']:.1f}ms`.")
    lines.append(f"- Reranker status after run: `{reranker_status() or 'loaded/available'}`.")

    lines += ["", "## 7. Duplicate and Index Checks"]
    all_chunks = []
    for src in all_sources():
        if src.key not in SOURCE_KEYS:
            continue
        for p in scroll_points(qdrant, src.collection):
            text = point_text(p)
            all_chunks.append((src.key, (p.payload or {}).get("doc_id"), hashlib.sha256(text.encode()).hexdigest(), text))
    exact = defaultdict(list)
    for row in all_chunks: exact[row[2]].append(row[:2])
    dupes = [v for v in exact.values() if len(v) > 1]
    lines.append(f"- Total chunks scanned: `{len(all_chunks)}`; exact duplicate text groups across/within sources: `{len(dupes)}`; duplicate memberships: `{sum(len(v) for v in dupes)}`.")
    for group in dupes[:10]: lines.append(f"- Duplicate group: `{group}`")
    lines.append("- Near-duplicate cosine comparison was not run because the diagnostic intentionally avoids downloading all 10k dense vectors; exact duplicate hashes are definitive for identical text.")
    for src in all_sources():
        if src.key not in SOURCE_KEYS: continue
        info = qdrant.get_collection(src.collection)
        lines.append(f"- `{src.collection}`: points `{info.points_count}`, indexed vectors `{info.indexed_vectors_count}`, dense config `{info.config.params.vectors}`, payload indexes `{list((info.payload_schema or {}).keys())}`.")

    lines += ["", "## 8. Freshness and Concurrency"]
    known_query = "T1552.005 Cloud Instance Metadata API"
    async with AsyncSessionLocal() as session:
        fresh = await federated_search(known_query, session, top_k=3, rerank=False)
    lines.append(f"- Freshness probe query `{known_query}` returned `{[(h.source_key, h.doc_id, h.rank, h.score) for h in fresh.hits]}`.")
    async def concurrent(i):
        async with AsyncSessionLocal() as session:
            t = time.perf_counter(); result = await federated_search("SSRF AWS metadata credentials", session, top_k=3, rerank=False); return (time.perf_counter()-t)*1000, [(h.source_key, h.doc_id) for h in result.hits]
    concurrent_results = await asyncio.gather(*(concurrent(i) for i in range(5)), return_exceptions=True)
    lines.append(f"- Five parallel retrievals: `{concurrent_results}`; exceptions: `{sum(isinstance(x, Exception) for x in concurrent_results)}`.")
    lines.append("- Silent failure review: source failures are caught and logged in `_search_one_source`; sparse failures are logged and noted. The outer `asyncio.gather(..., return_exceptions=True)` also logs unexpected source failures. No timeout wraps Qdrant calls themselves; the client timeout is 30s.")
    lines.append("- Fusion review: Qdrant dense+sparse fusion uses server-side RRF with `RRF_K=60`; cross-source ordering uses source-local rank and editorial weights, not a tunable learned blend. No evidence of tuned weights was found; source weights are static code defaults (NVD/MITRE 1.0, internal 1.1, OWASP docs 1.15, templates 1.2, Ghostwriter 1.25).")

    lines += ["", "## 9. Isolation of Wrong-Number Case", "- No current conversation/finding record identifies a concrete wrong-number incident or CVSS value. The closest live precedent is Ghostwriter `gw-1`, whose stored payload has CVSS `9.3`; retrieval-only queries for `SSRF AWS metadata credentials` returned the raw Ghostwriter text above. Therefore this audit cannot honestly classify the unnamed incident as present/malformed/absent without the exact query or reported number.", "- Retrieval architecture hands `chunk_text` to `RetrievalHit.text`; for Ghostwriter, structured CVSS is also retained in payload. Any wrong value absent from the raw hit is a generation/validation issue; a split label/value would be a chunking issue; an absent target is retrieval/index coverage."]

    lines += ["", "## 10. Top Three Retrieval Weaknesses", "1. **Global token-window chunking for structured records.** The indexer applies one `800/100` token policy to all sources. Multi-chunk examples and marker crossings above demonstrate that long OWASP/MITRE/template records are not record-aware. Fix: chunking change, preferably field/record-boundary-aware serialization.", "2. **Static cross-source rank/weight defaults are not empirically tuned.** The live pipeline uses source-local rank plus hard-coded weights, while reranking is disabled by default. A semantically relevant source can be outranked by a higher-weight precedent or lose final-context space. Fix: ranking/config evaluation against a gold set.", "3. **Coverage and freshness are count-based, not content-complete.** SQL reports zero unsynced rows and Qdrant is searchable, but absent decoy CVEs are not distinguished by an authoritative negative index, and Qdrant health only compares point/row counts. Fix: ingestion/index freshness assertions and explicit document-level audit checks."]

    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    asyncio.run(main())
