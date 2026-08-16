"""Print source synchronization and Qdrant dimension status."""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.db import AsyncSessionLocal
from app.ingestion.embedder import current_embed_signature
from app.kb.registry import all_sources
from app.services.retrieval import get_qdrant, init_qdrant_client


async def main() -> None:
    init_qdrant_client()
    qdrant = get_qdrant()
    signature = current_embed_signature()
    async with AsyncSessionLocal() as session:
        for source in all_sources():
            rows = int(
                (
                    await session.execute(
                        select(func.count()).select_from(source.model)
                    )
                ).scalar_one()
            )
            pending = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(source.model)
                        .where(source.model.embed_model.is_distinct_from(signature))
                    )
                ).scalar_one()
            )
            if qdrant.collection_exists(source.collection):
                info = qdrant.get_collection(source.collection)
                vectors = qdrant.count(source.collection, exact=True).count
                config = info.config.params.vectors
                dense = config["dense"] if isinstance(config, dict) else config
                dimension = dense.size
            else:
                vectors, dimension = 0, None
            print(
                f"{source.key}: rows={rows} pending={pending} "
                f"vectors={vectors} dimension={dimension}"
            )


if __name__ == "__main__":
    asyncio.run(main())
