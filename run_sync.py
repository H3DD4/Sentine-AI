# run_sync.py — project root (Mazars_rag/)
import asyncio
import logging

from app.db import get_session_context
from app.ingestion.nvd_sync import sync_nvd
from app.ingestion.mitre_sync import sync_mitre
from app.ingestion.embedder import load_model_sync, embed_text_mean
from app.services.retrieval import init_qdrant_client, get_qdrant
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger(__name__)


def ensure_collection() -> None:
    """Create the Qdrant collection if it doesn't exist."""
    from qdrant_client.models import Distance, VectorParams

    qdrant = get_qdrant()

    existing = [c.name for c in qdrant.get_collections().collections]
    if settings.QDRANT_COLLECTION in existing:
        log.info("Collection '%s' already exists — skipping creation.", settings.QDRANT_COLLECTION)
        return

    # Detect vector size from the embedding model
    sample_vector = embed_text_mean("test")
    vector_size = len(sample_vector)

    qdrant.create_collection(
        collection_name=settings.QDRANT_COLLECTION,
        vectors_config=VectorParams(
            size=vector_size,
            distance=Distance.COSINE,
        ),
    )
    log.info(
        "✅ Created Qdrant collection '%s' (size=%d, distance=COSINE).",
        settings.QDRANT_COLLECTION,
        vector_size,
    )


async def main() -> None:
    # ── Init ─────────────────────────────────────────────────
    log.info("Loading embedding model...")
    load_model_sync()

    log.info("Initializing Qdrant client...")
    init_qdrant_client()

    log.info("Ensuring Qdrant collection exists...")
    ensure_collection()

    # ── NVD ──────────────────────────────────────────────────
    log.info("⏳ Syncing NVD (30 days back)...")
    try:
        async with get_session_context() as session:
            await sync_nvd(session, days_back=30)
        log.info("✅ NVD sync complete.")
    except Exception:
        log.exception("❌ NVD sync failed — continuing to MITRE.")

    # ── MITRE ─────────────────────────────────────────────────
    log.info("⏳ Syncing MITRE...")
    try:
        async with get_session_context() as session:
            await sync_mitre(session)
        log.info("✅ MITRE sync complete.")
    except Exception:
        log.exception("❌ MITRE sync failed.")


if __name__ == "__main__":
    asyncio.run(main())