r"""Run repeated live retrieval checks with fail-closed assertions.

Run:
    .venv\Scripts\python.exe -m scripts.retrieval_stress_test
"""

from __future__ import annotations

import asyncio
import json
import time

from app.db import AsyncSessionLocal
from app.services.retrieval import federated_search


CASES = [
    ("What is CVE-2023-27159?", "nvd", "CVE-2023-27159"),
    ("Explain CVE-2023-27160 and its affected product", "nvd", "CVE-2023-27160"),
    ("Assess CVE-2023-27161", "nvd", "CVE-2023-27161"),
    ("How is T1552.005 detected?", "mitre", "T1552.005"),
    ("Describe T1059.001 PowerShell", "mitre", "T1059.001"),
    ("What is T1190?", "mitre", "T1190"),
    ("cloud metadata credential access technique", "mitre", "T1552.005"),
    ("Use template V_006", "finding_templates", "V_006"),
    ("Find ASIA_V_009", "finding_templates", "ASIA_V_009"),
    ("SSRF fetching AWS instance metadata credentials", "ghostwriter", "gw-1"),
]


async def run() -> None:
    all_passed = True
    reports = []
    async with AsyncSessionLocal() as session:
        for pass_number in range(1, 4):
            for query, source, expected in CASES:
                started = time.perf_counter()
                outcome = await federated_search(query, session, top_k=10, rerank=False)
                elapsed = round((time.perf_counter() - started) * 1000, 1)
                hits = [
                    (h.source_key, h.doc_id, h.payload.get("template_code"), h.score)
                    for h in outcome.hits
                ]
                found = any(
                    s == source and (doc == expected or template == expected)
                    for s, doc, template, _ in hits
                )
                # Every returned passage must carry its identity after reindexing.
                self_describing = all(
                    f"Document ID: {h.doc_id}" in h.text for h in outcome.hits if h.text
                )
                passed = found and self_describing
                all_passed &= passed
                reports.append({
                    "pass": pass_number,
                    "query": query,
                    "expected": [source, expected],
                    "passed": passed,
                    "rank": next((i + 1 for i, (s, doc, template, _) in enumerate(hits)
                                  if s == source and (doc == expected or template == expected)), None),
                    "latency_ms": elapsed,
                    "hits": hits,
                    "notes": outcome.notes,
                })
    print(json.dumps({"passed": all_passed, "cases": reports}, indent=2, default=str))
    if not all_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(run())
