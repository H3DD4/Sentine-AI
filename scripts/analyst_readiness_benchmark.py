"""Realistic pentester benchmark for the final grounding decision.

This sends 60 analyst-style prompts through the real structured model path. Each
case has deterministic expectations derived from its supplied evidence, rather
than an expected prose string. Retrieval uses only isolated pilot collections.
The script writes a full JSON report and never modifies production data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import os
from pathlib import Path

from app.kb.base import Availability, RetrievalHit, SearchOutcome, SourceReport
from app.kb.registry import get_source
from app.services.chat_grounding import generate_structured_response
from app.services.llm_client import AsyncLLMClient
from scripts.pilot_french_ssrf_grounding_test import _query_collection
from app.services.retrieval import get_qdrant, init_qdrant_client
from app.ingestion.embedder import load_model_sync, load_sparse_model_sync


ID_RE = re.compile(
    r"\b(?:CVE-\d{4}-\d{4,}|CWE-\d+|T\d{4}(?:\.\d{3})?|"
    r"A(?:0[1-9]|10):20\d{2}|(?:(?:TII|TIS|ASIA)_)?(?:BP|V)_\d{3})\b",
    re.IGNORECASE,
)


def _outcome(hits, prompt: str) -> SearchOutcome:
    normalized = []
    seen = set()
    for item in hits:
        payload = item.point.payload or {}
        doc_id = str(payload.get("doc_id") or "")
        key = (item.source, doc_id)
        if key in seen:
            continue
        seen.add(key)
        source = get_source(item.source)
        normalized.append(RetrievalHit(
            source_key=item.source,
            source_label=source.display_name,
            doc_id=doc_id,
            title=str(payload.get("title") or doc_id),
            text=str(payload.get("chunk_text") or payload.get("description") or ""),
            score=float(item.point.score or 0),
            payload=payload,
        ))
    normalized.sort(key=lambda h: h.score, reverse=True)
    normalized = normalized[:15]
    reports = [
        SourceReport(key, get_source(key).display_name, Availability.OK,
                     hits=sum(h.source_key == key for h in normalized))
        for key in sorted({h.source_key for h in normalized})
    ]
    return SearchOutcome(hits=normalized, reports=reports, query=prompt)


BASE_SCENARIOS = [
    {
        "name": "ssrf_metadata",
        "query": "server retrieves remote URL and exposes cloud instance metadata credentials",
        "evidence": "The tester changed a server-side template URL to an internal metadata endpoint. The server made the outbound request and the generated document contained a temporary token.",
        "must": ["token", "conditional", "pending"],
        "forbid_score": True,
        "language": "fr",
    },
    {
        "name": "jwt_confusion",
        "query": "JWT algorithm confusion weak signature validation public key used as HMAC secret",
        "evidence": "Testing showed the verifier accepted a token signed with a different algorithm using key material intended for another algorithm. No production account takeover was demonstrated.",
        "must": ["conditional", "pending"],
        "forbid_score": True,
        "language": "en",
    },
    {
        "name": "buffer_overflow",
        "query": "buffer overflow local user executes arbitrary commands in a library",
        "evidence": "A local standard user reproduced a crash with an oversized input. No proof of code execution or privilege escalation was obtained.",
        "must": ["crash", "no proof", "pending"],
        "forbid_score": True,
        "language": "en",
    },
    {
        "name": "cors",
        "query": "insecure CORS arbitrary origins credentials sensitive response",
        "evidence": "An HTTP response accepted an untrusted Origin and returned a permissive access-control header. The tester did not prove that authenticated browser credentials were included.",
        "must": ["cors", "not prove", "pending"],
        "forbid_score": True,
        "language": "fr",
    },
    {
        "name": "auth_bypass",
        "query": "HTTP basic authentication bypass direct access protected page",
        "evidence": "The tester reached a page without passing the expected authentication gate. No administrative action was performed.",
        "must": ["authentication", "no administrative", "pending"],
        "forbid_score": True,
        "language": "en",
    },
    {
        "name": "contradiction",
        "query": "server side request forgery prevention URL allowlist redirect validation",
        "evidence": "Tester A retrieved an internal response. Tester B received a timeout in a separate session and could not confirm the same environment. The team claims egress filtering but supplied no rule or log.",
        "must": ["timeout", "environment", "pending"],
        "forbid_score": True,
        "language": "fr",
    },
    {
        "name": "cvss_complete",
        "query": "server-side request forgery remote URL fetch internal service",
        "evidence": "The unauthenticated network attacker can send one request, needs no user interaction, and the server discloses confidential data. Integrity and availability are unchanged. The authority boundary is unchanged.",
        "cvss_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N",
        "must": ["cvss", "7.5"],
        "require_exact_cvss": True,
        "language": "en",
    },
    {
        "name": "scope_ambiguous",
        "query": "temporary credentials cloud metadata object storage read access",
        "evidence": "A token was returned, and the application team says it has read permission on object storage. The storage trust boundary, tenant, and actual token use were not tested.",
        "must": ["scope", "unknown", "pending"],
        "forbid_score": True,
        "language": "fr",
    },
    {
        "name": "negative_mapping",
        "query": "remote URL fetch SSRF arbitrary callback redirects",
        "evidence": "The observed behavior is server-side URL fetching. There is no evidence of a specific product, version, vendor, or published vulnerability record.",
        "must": ["no exact", "not available", "pending"],
        "forbid_score": True,
        "language": "en",
    },
    {
        "name": "severity_request",
        "query": "server-side request forgery credential exposure",
        "evidence": "The server returned a temporary credential in a generated document. The accessible data, permissions, and repeatability are not fully verified.",
        "must": ["pending", "not determine"],
        "forbid_score": True,
        "language": "fr",
    },
    {
        "name": "remediation_only",
        "query": "SSRF prevention URL validation allowlist disable redirects",
        "evidence": "A remote resource URL is controlled by a standard user and the server follows redirects.",
        "must": ["allow", "redirect"],
        "forbid_score": True,
        "language": "en",
    },
    {
        "name": "mixed_evidence",
        "query": "credential exposure metadata API SSRF JWT token",
        "evidence": "TEXT1: The response contained a token-like string. TEXT2: The analyst could not validate its signature. IMAGE1: A screenshot shows a redacted storage console. Do not treat the redacted screenshot as proof of access.",
        "must": ["redacted", "not proof", "pending"],
        "forbid_score": True,
        "language": "fr",
    },
]


def _prompt(base: dict, variant: int) -> str:
    lang = base["language"]
    if lang == "fr":
        opening = "Tu es un pentester senior. Réponds en français et sépare faits observés, déductions, hypothèses et inconnues."
    else:
        opening = "You are a senior penetration tester. Separate observed facts, deductions, assumptions, and unknowns."
    modes = [
        "Produce a professional finding with title, severity, evidence, limitations, and remediation.",
        "Challenge the evidence and explicitly reject anything not demonstrated.",
        "Ask whether a CVSS score is justified. Do not guess missing metrics.",
        "Map only exact records supported by the retrieved evidence; otherwise say no exact match.",
        "Review this as a skeptical report reviewer and identify overclaims.",
    ]
    request = modes[variant % len(modes)]
    vector = f"\nThe analyst supplied this complete vector and permits calculation: {base['cvss_vector']}" if base.get("cvss_vector") else ""
    return f"{opening}\n\n{base['evidence']}\n\n{request}{vector}\n\nUse only local retrieved context. Do not invent identifiers, scores, permissions, exploit success, or impact."


async def main() -> None:
    load_model_sync()
    load_sparse_model_sync()
    init_qdrant_client()
    qdrant = get_qdrant()
    results = []
    failures = []
    delay_seconds = float(os.getenv("ANALYST_BENCHMARK_DELAY_SECONDS", "0"))
    case_limit = int(os.getenv("ANALYST_BENCHMARK_CASE_LIMIT", "0"))
    completed_cases = 0
    for base in BASE_SCENARIOS:
        for variant in range(5):
            if case_limit and completed_cases >= case_limit:
                break
            label = f"{base['name']}.{variant + 1}"
            prompt = _prompt(base, variant)
            collected = []
            for source_key in ("nvd", "mitre", "owasp", "owasp_docs", "finding_templates"):
                collection = f"pilot_{get_source(source_key).collection}_final"
                if await asyncio.to_thread(qdrant.collection_exists, collection):
                    collected.extend(await _query_collection(qdrant, source_key, base["query"]))
            outcome = _outcome(collected, prompt)
            started = time.perf_counter()
            try:
                client = AsyncLLMClient()
                response, grounding = await generate_structured_response(
                    client, [{"role": "user", "content": prompt}], outcome
                )
                lower = response.lower()
                cvss = grounding.draft.cvss
                retrieved_ids = {hit.doc_id.upper() for hit in outcome.hits}
                rendered_ids = {match.group(0).upper() for match in ID_RE.finditer(response)}
                unsupported_ids = sorted(rendered_ids - retrieved_ids - {base.get("cvss_vector", "").upper()})
                checks = {
                    "nonempty": bool(response.strip()),
                    "not_fallback": "could not be converted" not in lower,
                    "required_terms": all(term.lower() in lower for term in base["must"]),
                    "no_score_when_forbidden": not base.get("forbid_score") or cvss.score is None,
                    "exact_cvss_when_required": not base.get("require_exact_cvss") or (cvss.status == "exact" and cvss.score is not None),
                    "no_unsupported_ids": not unsupported_ids,
                    "grounding_not_unbounded": len(grounding.issues) <= 8,
                }
                passed = all(checks.values())
                result = {
                    "label": label,
                    "base": base["name"],
                    "variant": variant + 1,
                    "provider": client.last_generation,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "passed": passed,
                    "checks": checks,
                    "unsupported_ids": unsupported_ids,
                    "grounding_issues": grounding.issues,
                    "cvss_status": cvss.status,
                    "cvss_score": cvss.score,
                    "sources": outcome.sources_used,
                    "response": response,
                }
            except Exception as exc:
                result = {
                    "label": label, "base": base["name"], "variant": variant + 1,
                    "passed": False, "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            results.append(result)
            completed_cases += 1
            if not result["passed"]:
                failures.append(result)
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
        if case_limit and completed_cases >= case_limit:
            break

    report = {
        "case_count": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": len(failures),
        "pass_rate": round(sum(1 for r in results if r["passed"]) / max(len(results), 1), 4),
        "failure_categories": {
            "model_or_validation": sum(1 for r in failures if r.get("grounding_issues")),
            "transport_or_schema": sum(1 for r in failures if r.get("error")),
            "unsupported_identifier": sum(1 for r in failures if r.get("unsupported_ids")),
            "expectation_or_content": sum(1 for r in failures if r.get("checks", {}).get("required_terms") is False),
        },
        "provider_unavailable": sum(
            1 for r in results if "cooling down" in str(r.get("error", "")).lower()
            or "temporarily unavailable" in str(r.get("error", "")).lower()
            or "rate limit" in str(r.get("error", "")).lower()
            or "too many requests" in str(r.get("error", "")).lower()
        ),
        "production_decision": "BLOCKED" if failures else "CONDITIONAL_REVIEW_REQUIRED",
        "results": results,
    }
    path = Path("analyst_readiness_benchmark_report.json")
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("case_count", "passed", "failed", "pass_rate", "failure_categories", "production_decision")}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    argparse.ArgumentParser().parse_args()
    asyncio.run(main())
