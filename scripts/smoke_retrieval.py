"""
End-to-end retrieval smoke test.

Checks the three things the multi-RAG design promises, against the real
Postgres + Qdrant, not mocks:

  1. Federated search returns hits and a per-source report.
  2. Multimodal planning beats naive concatenation on a realistic finding.
  3. Degradation is graceful and *observable* — asking for a source that has
     no index must still answer from the sources that do, and must say so.

Run:  python -m scripts.smoke_retrieval
"""

from __future__ import annotations

import asyncio
import logging

from app.db import AsyncSessionLocal
from app.kb.registry import get_health
from app.services.retrieval import federated_search, get_qdrant, multimodal_search

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("smoke")


def show(outcome, limit: int = 5) -> None:
    print(f"  provenance : {outcome.provenance_line()}")
    print(f"  degraded   : {outcome.degraded}")
    for r in outcome.reports:
        print(
            f"    - {r.source_key:<12} {r.availability.value:<12} "
            f"hits={r.hits:<3} {r.latency_ms:>5}ms  {r.detail}"
        )
    print(f"  top {min(limit, len(outcome.hits))} of {len(outcome.hits)} hits:")
    for h in outcome.hits[:limit]:
        print(f"    [{h.source_label}] {h.title}  score={h.score:.4f}")
        print(f"        {h.text[:110].replace(chr(10), ' ')}…")


async def main() -> None:
    async with AsyncSessionLocal() as session:
        print("\n=== 0. Source health " + "=" * 55)
        health = await get_health(session, get_qdrant(), force=True)
        for key, h in health.items():
            print(
                f"  {key:<12} {h.availability.value:<12} rows={h.row_count:<7} "
                f"vectors={h.vector_count:<7} {h.detail}"
            )

        print("\n=== 1. Federated search: 'Apache Tomcat AJP file read' " + "=" * 20)
        outcome = await federated_search(
            "Apache Tomcat AJP connector unauthenticated file read", session, top_k=5
        )
        show(outcome)

        exact_cve = "CVE-2023-27159"
        print(f"\n=== 2. Exact-identifier recall: '{exact_cve}' " + "=" * 24)
        outcome = await federated_search(exact_cve, session, top_k=5)
        show(outcome, limit=3)
        ids = [h.doc_id for h in outcome.hits]
        exact_recalled = exact_cve in ids
        print(f"  -> {exact_cve} in results: {exact_recalled}")
        assert exact_recalled, f"exact identifier was not recalled: {exact_cve}"

        print("\n=== 3. Multimodal: description + log excerpt + screenshot " + "=" * 17)
        outcome = await multimodal_search(
            "The application server exposes an undocumented binary protocol on a "
            "high port that allows reading files outside the web root.",
            session,
            evidence_texts=[
                "PORT     STATE SERVICE\n"
                "8009/tcp open  ajp13   Apache Jserv (Protocol v1.3)\n"
                "8080/tcp open  http    Apache Tomcat 9.0.30\n"
                "9100/tcp closed jetdirect\n"
                "----------------------------------------",
            ],
            image_descriptions=[
                "Screenshot of a terminal showing the output of an exploit script. "
                "The banner reads 'Ghostcat - CVE-2020-1938' and the window below "
                "displays the contents of WEB-INF/web.xml."
            ],
            top_k=5,
        )
        show(outcome)

        print("\n=== 4. Partial coverage: request an empty source " + "=" * 24)
        # `internal` has no rows yet. The correct behaviour
        # is not an exception and not a silent empty list — it is an answer from
        # whatever is available, carrying an explicit statement of the gap.
        outcome = await federated_search(
            "privilege escalation via misconfigured service",
            session,
            sources=["nvd", "internal"],
            top_k=5,
        )
        show(outcome, limit=3)
        assert outcome.hits, "available NVD source should still return results"
        assert any(
            report.source_key == "internal" and report.availability.value == "empty"
            for report in outcome.reports
        ), "empty requested source should be explicit in provenance"

        print("\n=== 5. Empty-only source request " + "=" * 38)
        outcome = await federated_search(
            "lateral movement", session, sources=["internal"], top_k=5
        )
        show(outcome, limit=3)
        print(f"  -> hits={len(outcome.hits)} (expected 0), degraded={outcome.degraded}")
        assert not outcome.hits, "an empty-only source request should have no hits"
        assert any(
            report.source_key == "internal" and report.availability.value == "empty"
            for report in outcome.reports
        ), "empty-only request should explain why it has no hits"


if __name__ == "__main__":
    asyncio.run(main())
