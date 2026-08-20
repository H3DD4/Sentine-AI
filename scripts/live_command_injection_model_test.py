"""Run a live grounded model test for an OS command-injection finding."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time

from app.db import AsyncSessionLocal
from app.services.chat_grounding import generate_structured_response
from app.services.llm_client import AsyncLLMClient
from app.services.retrieval import multimodal_search


PROMPT = """Assess this new finding as a red-team reviewer. An unauthenticated
POST /api/export accepts a filename. The request used filename=report.csv;id and
the HTTP response contained uid=1001(app) gid=1001(app). A control request with
filename=report.csv returned only CSV data. The service runs in a Linux
container. No product name, version, CVE, persistence, privilege escalation, or
access to secrets has been demonstrated. Separate observed facts from
assumptions, classify the weakness only when supported, propose concrete
remediation and verification steps, do not invent identifiers, and keep CVSS
pending unless every required metric is justified."""

AUTHORITATIVE_ID = re.compile(
    r"(?:CVE-\d{4}-\d{4,7}|T\d{4}(?:\.\d{3})?|CWE-\d+)", re.IGNORECASE
)


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    async with AsyncSessionLocal() as session:
        outcome = await multimodal_search(
            "Unauthenticated export filename parameter executes shell "
            "metacharacters and returns operating-system command output",
            session,
            evidence_texts=[
                "POST /api/export HTTP/1.1\nContent-Type: application/json\n\n"
                '{"filename":"report.csv;id"}\n\nHTTP/1.1 200 OK\n'
                "uid=1001(app) gid=1001(app) groups=1001(app)",
                "Control request: filename=report.csv -> response contains CSV "
                "records only; no uid/gid output.",
            ],
            image_descriptions=[
                "Terminal capture showing the export HTTP response with "
                "uid=1001(app), while the comparison response contains only CSV output."
            ],
            top_k=8,
        )

        client = AsyncLLMClient()
        started = time.perf_counter()
        response, grounding = await generate_structured_response(
            client,
            [{"role": "user", "content": PROMPT}],
            outcome,
        )

    retrieved = {hit.doc_id.upper() for hit in outcome.hits}
    rendered = {match.group(0).upper() for match in AUTHORITATIVE_ID.finditer(response)}
    unsupported = sorted(
        identifier
        for identifier in rendered
        if identifier not in retrieved and not identifier.startswith("CWE-")
    )
    checks = {
        "model_contributed": grounding.generation_status in {"model", "corrected_model"},
        "preserves_observed_command_output": "uid=1001" in response.lower(),
        "recognizes_command_injection": (
            "command injection" in response.lower()
            or "os command" in response.lower()
        ),
        "does_not_claim_proven_privilege_escalation": (
            "privilege escalation was demonstrated" not in response.lower()
        ),
        "cvss_not_fabricated": grounding.draft.cvss.score is None,
        "no_unsupported_authoritative_ids": not unsupported,
        "has_provenance": "Knowledge sources used:" in response,
    }
    result = {
        "passed": all(checks.values()),
        "provider": client.last_generation,
        "finish_reason": client.last_finish_reason,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "generation_status": grounding.generation_status,
        "corrected": grounding.corrected,
        "grounding_issues": grounding.issues,
        "cvss_status": grounding.draft.cvss.status,
        "cvss_score": grounding.draft.cvss.score,
        "retrieval_degraded": outcome.degraded,
        "sources": outcome.sources_used,
        "retrieved_ids": sorted(retrieved),
        "unsupported_authoritative_ids": unsupported,
        "checks": checks,
        "response": response,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
