"""Initialize the Docker stack database, local KB sources, and derived indexes."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from app.db import AsyncSessionLocal
from app.ingestion.finding_templates_sync import sync_finding_templates
from app.ingestion.mitre_sync import sync_mitre
from app.ingestion.nvd_sync import sync_nvd
from app.ingestion.owasp_docs_sync import sync_owasp_documents
from app.ingestion.owasp_sync import sync_owasp_top10
from app.kb.indexer import ensure_all_collections
from app.kb.registry import all_sources, get_health
from app.services.retrieval import get_qdrant, init_qdrant_client

log = logging.getLogger("docker-init")


async def main() -> None:
    seed_dir = Path(os.getenv("RETRIEVAL_SEED_DIR", "/app/seed/retrieval"))
    if os.getenv("RESTORE_RETRIEVAL_SEED", "true").lower() == "true":
        manifest = seed_dir / "manifest.json"
        if not manifest.exists():
            raise RuntimeError(f"Retrieval seed is missing: {manifest}")
        from scripts.restore_retrieval_seed import main as restore_seed

        log.info("Restoring bundled retrieval seed from %s", seed_dir)
        await restore_seed(seed_dir)
    else:
        log.info("Bundled retrieval seed restore disabled")

    init_qdrant_client()
    qdrant = get_qdrant()
    await ensure_all_collections(qdrant, all_sources())
    async with AsyncSessionLocal() as session:
        if os.getenv("SEED_NVD", "false").lower() == "true":
            log.info("Synchronizing recently modified NVD records")
            await sync_nvd(session, days_back=int(os.getenv("NVD_DAYS_BACK", "30")))
        if os.getenv("SEED_MITRE", "true").lower() == "true":
            log.info("Synchronizing MITRE ATT&CK")
            await sync_mitre(session)
        if os.getenv("SEED_OWASP", "true").lower() == "true":
            log.info("Synchronizing OWASP Top 10 and official guides")
            await sync_owasp_top10(session)
            await sync_owasp_documents(session)
        if os.getenv("SEED_FINDING_TEMPLATES", "true").lower() == "true":
            log.info("Synchronizing repository finding templates")
            await sync_finding_templates(session)
        health = await get_health(session, qdrant, force=True)
        for key, value in health.items():
            log.info("%s: %s (%s)", key, value.availability.value, value.detail)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
