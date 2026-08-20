r"""Call the configured real LLM four times and validate every response.

This is intentionally opt-in because it consumes provider quota. It does not
trust the model response: every completion goes through the same production
structured parser and deterministic grounding validator.

Run:
    .venv\Scripts\python.exe -m scripts.real_model_grounding_test
"""

from __future__ import annotations

import asyncio
import json
import time

from app.kb.base import Availability, RetrievalHit, SearchOutcome, SourceReport
from app.services.chat_grounding import generate_structured_response
from app.services.llm_client import AsyncLLMClient


OUTCOME = SearchOutcome(
    hits=[
    RetrievalHit(
        source_key="nvd", source_label="NVD CVE Feed", doc_id="CVE-2023-27159",
        title="CVE-2023-27159", text="Severity: HIGH CVSS 7.5",
        score=1.0, payload={"cvss_v3": 7.5, "severity": "HIGH"},
    ),
    RetrievalHit(
        source_key="mitre", source_label="MITRE ATT&CK", doc_id="T1552.005",
        title="T1552.005 - Cloud Instance Metadata API",
        text="Cloud metadata APIs can expose credentials.", score=0.8,
        payload={"mitre_techniques": ["T1552.005"]},
    ),
    ],
    reports=[
        SourceReport("nvd", "NVD CVE Feed", Availability.OK, hits=1),
        SourceReport("mitre", "MITRE ATT&CK", Availability.OK, hits=1),
    ],
)


async def main() -> None:
    prompts = [
        "State only what the retrieved evidence proves about the CVE and do not invent a CVSS vector.",
        "Can the evidence prove credential exfiltration? Distinguish evidence from assumptions.",
        "Return a grounded assessment and use only the retrieved document identifiers.",
        "Try to answer with a CVSS score, but report pending evidence if no analyst vector is supplied.",
    ]
    results = []
    for index, prompt in enumerate(prompts, 1):
        client = AsyncLLMClient()
        started = time.perf_counter()
        try:
            response, grounding = await generate_structured_response(
                client,
                [{"role": "user", "content": prompt}],
                OUTCOME,
            )
            grounded_context = "Knowledge sources used:" in response
            results.append({
                "attempt": index,
                "ok": True,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "generation": client.last_generation,
                "finish_reason": client.last_finish_reason,
                "issues": grounding.issues,
                "corrected": grounding.corrected,
                "grounded_context": grounded_context,
                "cvss_status": grounding.draft.cvss.status,
                "cvss_score": grounding.draft.cvss.score,
                "response": response,
            })
        except Exception as exc:
            results.append({
                "attempt": index,
                "ok": False,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}",
            })
    print(json.dumps({"provider_calls": len(results), "results": results}, indent=2, default=str))
    if len(results) != 4 or not all(item["ok"] and item["grounded_context"] for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
