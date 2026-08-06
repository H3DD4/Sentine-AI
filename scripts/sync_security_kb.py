"""Populate public security knowledge sources and their Qdrant indexes.

Examples:
    python -m scripts.sync_security_kb
    python -m scripts.sync_security_kb --source mitre
    python -m scripts.sync_security_kb --source owasp  # all official releases since 2021
    python -m scripts.sync_security_kb --source owasp-docs
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import func, select

from app.db import AsyncSessionLocal
from app.ingestion.mitre_sync import sync_mitre
from app.ingestion.owasp_sync import sync_owasp_top10
from app.ingestion.owasp_docs_sync import sync_owasp_documents
from app.kb.indexer import ensure_all_collections
from app.kb.models import OwaspDocument, OwaspTop10Entry
from app.kb.registry import all_sources, get_health
from app.services.retrieval import get_qdrant, init_qdrant_client


async def main(source: str) -> None:
    init_qdrant_client()
    qdrant = get_qdrant()
    await ensure_all_collections(qdrant, all_sources())

    async with AsyncSessionLocal() as session:
        if source in ("all", "owasp"):
            print("Syncing OWASP Top 10...")
            print(await sync_owasp_top10(session))
        if source in ("all", "owasp", "owasp-docs"):
            print("Syncing OWASP official guides...")
            print(await sync_owasp_documents(session))
        if source in ("all", "mitre"):
            print("Syncing MITRE ATT&CK...")
            print(await sync_mitre(session))

        health = await get_health(session, qdrant, force=True)
        print("\nSource health:")
        for key in ("owasp", "owasp_docs", "mitre"):
            state = health[key]
            print(
                f"  {key}: {state.availability.value}, "
                f"rows={state.row_count}, vectors={state.vector_count}, {state.detail}"
            )

        if source in ("all", "owasp", "owasp-docs"):
            top10 = list(
                (
                    await session.execute(
                        select(OwaspTop10Entry.year, func.count())
                        .group_by(OwaspTop10Entry.year)
                        .order_by(OwaspTop10Entry.year)
                    )
                ).all()
            )
            guides = list(
                (
                    await session.execute(
                        select(OwaspDocument.project, func.count())
                        .group_by(OwaspDocument.project)
                        .order_by(OwaspDocument.project)
                    )
                ).all()
            )
            print("\nOWASP coverage:")
            print("  Top 10 editions: " + ", ".join(f"{year}={count}" for year, count in top10))
            print("  Official guides: " + ", ".join(f"{project}={count}" for project, count in guides))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", choices=("all", "owasp", "owasp-docs", "mitre"), default="all"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main(args.source))
