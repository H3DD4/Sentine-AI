"""Run three real adversarial analyst cases through retrieval and grounding."""

from __future__ import annotations

import asyncio
import json
import time

from app.ingestion.embedder import load_model_sync, load_sparse_model_sync
from app.kb.registry import get_source
from app.services.chat_grounding import generate_structured_response
from app.services.llm_client import AsyncLLMClient
from app.services.retrieval import get_qdrant, init_qdrant_client
from scripts.analyst_readiness_benchmark import BASE_SCENARIOS, ID_RE, _outcome, _prompt
from scripts.pilot_french_ssrf_grounding_test import _query_collection


CASES = ["contradiction", "scope_ambiguous", "cvss_complete"]


async def main() -> None:
    load_model_sync()
    load_sparse_model_sync()
    init_qdrant_client()
    qdrant = get_qdrant()
    selected = [base for base in BASE_SCENARIOS if base["name"] in CASES]
    results = []

    for base in selected:
        prompt = _prompt(base, 2)
        collected = []
        for source_key in ("nvd", "mitre", "owasp", "owasp_docs", "finding_templates"):
            collection = f"pilot_{get_source(source_key).collection}_final"
            if await asyncio.to_thread(qdrant.collection_exists, collection):
                collected.extend(await _query_collection(qdrant, source_key, base["query"]))
        outcome = _outcome(collected, prompt)
        started = time.perf_counter()
        client = AsyncLLMClient()
        try:
            response, grounding = await generate_structured_response(
                client, [{"role": "user", "content": prompt}], outcome
            )
            cvss = grounding.draft.cvss
            lower = response.lower()
            retrieved_ids = {hit.doc_id.upper() for hit in outcome.hits}
            rendered_ids = {match.group(0).upper() for match in ID_RE.finditer(response)}
            unsupported_ids = sorted(
                rendered_ids - retrieved_ids - {base.get("cvss_vector", "").upper()}
            )
            checks = {
                "nonempty": bool(response.strip()),
                "required_terms": all(term.lower() in lower for term in base["must"]),
                "no_score_when_forbidden": not base.get("forbid_score") or cvss.score is None,
                "exact_cvss_when_required": not base.get("require_exact_cvss") or (
                    cvss.status == "exact" and cvss.score is not None
                ),
                "no_unsupported_ids": not unsupported_ids,
                "grounding_issues_bounded": len(grounding.issues) <= 8,
            }
            results.append({
                "case": base["name"],
                "passed": all(checks.values()),
                "checks": checks,
                "unsupported_ids": unsupported_ids,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "provider": client.last_generation,
                "cvss_status": cvss.status,
                "cvss_score": cvss.score,
                "grounding_issues": grounding.issues,
                "sources": outcome.sources_used,
                "response": response,
            })
        except Exception as exc:
            results.append({
                "case": base["name"],
                "passed": False,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}",
            })

    report = {
        "case_count": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "average_latency_ms": round(sum(item["latency_ms"] for item in results) / len(results), 1),
        "results": results,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    if report["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
