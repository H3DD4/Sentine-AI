"""Score one hard case by final system behavior, independent of model identity."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time

from app.ingestion.embedder import load_model_sync, load_sparse_model_sync
from app.kb.registry import get_source
from app.services.chat_grounding import generate_structured_response
from app.services.llm_client import AsyncLLMClient
from app.services.retrieval import get_qdrant, init_qdrant_client
from scripts.pilot_french_ssrf_grounding_test import (
    AUTHORITATIVE,
    PROMPT,
    _query_collection,
    _to_outcome,
)


def _check(name: str, value: bool, detail: str = "") -> dict:
    return {"name": name, "passed": bool(value), "detail": detail}


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_model_sync()
    load_sparse_model_sync()
    init_qdrant_client()
    qdrant = get_qdrant()
    collected = []
    query_parts = [
        "server retrieves a remote URL and exposes cloud instance metadata credentials",
        "server-side request forgery prevention validate URL allowlist redirects",
        "webhook callback accepts arbitrary destination and follows redirects",
        "temporary credentials exposed through a remote resource fetch",
    ]
    for query in query_parts:
        for source_key in ("nvd", "mitre", "owasp", "owasp_docs", "finding_templates"):
            collection = f"pilot_{get_source(source_key).collection}_final"
            if await asyncio.to_thread(qdrant.collection_exists, collection):
                collected.extend(await _query_collection(qdrant, source_key, query))

    outcome = _to_outcome(collected, PROMPT)
    started = time.perf_counter()
    client = AsyncLLMClient()
    error = None
    response = ""
    grounding = None
    try:
        response, grounding = await generate_structured_response(
            client, [{"role": "user", "content": PROMPT}], outcome
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    lower = response.lower()
    retrieved_ids = {hit.doc_id.upper() for hit in outcome.hits}
    leaked_ids = {
        match.group(0).upper()
        for match in AUTHORITATIVE.finditer(response)
        if match.group(0).upper() not in retrieved_ids
    }
    issues = grounding.issues if grounding else [error or "no grounding result"]
    cvss = grounding.draft.cvss if grounding else None
    checks = [
        _check("retrieval_has_hits", bool(outcome.hits), f"hits={len(outcome.hits)}"),
        _check("retrieval_has_ssrf_evidence", any("ssrf" in h.text.lower() or "server side request forgery" in h.text.lower() for h in outcome.hits)),
        _check("final_answer_nonempty", bool(response.strip())),
        _check("identifies_ssrf", "ssrf" in lower or "server-side request forgery" in lower or "server side request forgery" in lower),
        _check("preserves_observed_facts", all(term in lower for term in ("template_source", "jeton", "utilisateur standard"))),
        _check("does_not_deny_supplied_evidence", not any(term in lower for term in (
            "aucune preuve directe", "no direct evidence", "aucune preuve observée",
            "aucune preuve documentaire", "no documentary evidence",
        ))),
        _check("preserves_contradiction", any(term in lower for term in ("contradiction", "timeout", "contradic"))),
        _check("addresses_scope", any(term in lower for term in ("scope", "portée", "portee", "cvss"))),
        _check("cvss_is_conservative", bool(cvss and cvss.score is None and cvss.status not in {"exact", "range"})),
        _check("no_unsupported_identifiers", not leaked_ids, ", ".join(sorted(leaked_ids))),
        _check("safe_renderer_completed", grounding is not None and "Knowledge sources used" in response),
        _check("grounding_is_bounded", grounding is not None and len(issues) <= 8, f"issues={len(issues)}"),
        _check("no_provider_error", error is None),
    ]
    passed = sum(item["passed"] for item in checks)
    system_score = round(100 * passed / len(checks), 1)
    generation_status = grounding.generation_status if grounding else None
    model_contributed = generation_status in {"model", "corrected_model"}
    useful_model_answer = model_contributed and all(
        item["passed"] for item in checks
        if item["name"] in {
            "final_answer_nonempty", "identifies_ssrf", "preserves_observed_facts",
            "preserves_contradiction", "addresses_scope", "no_unsupported_identifiers",
        }
    )
    model_score = system_score if model_contributed else None
    degraded_mode_score = system_score if generation_status == "deterministic_fallback" else None
    report = {
        "model": client.last_generation,
        "model_available": client.last_generation is not None,
        "model_contributed": model_contributed,
        "generation_status": generation_status,
        "model_score": model_score,
        "degraded_mode_score": degraded_mode_score,
        "system_score": system_score,
        "passed_checks": passed,
        "check_count": len(checks),
        "checks": checks,
        "latency_ms": latency_ms,
        "grounding_issues": issues,
        "cvss_status": cvss.status if cvss else None,
        "cvss_score": cvss.score if cvss else None,
        "retrieved_ids": sorted(retrieved_ids),
        "response": response,
        "error": error,
        "single_case_gate": "candidate_pass" if (
            useful_model_answer
            and system_score > 70
            and not leaked_ids
            and error is None
            and bool(response.strip())
        ) else "blocked",
        "production_migration_gate": "blocked_pending_repeated_multi_case_validation",
    }
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
