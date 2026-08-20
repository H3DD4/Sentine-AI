"""Report SQL signature coverage and Qdrant point counts for every source."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import func, select

from app.db import AsyncSessionLocal
from app.ingestion.embedder import current_embed_signature
from app.kb.registry import all_sources
from app.services.retrieval import get_qdrant


async def main() -> None:
    signature = current_embed_signature()
    result = {"expected_signature": signature, "sources": []}
    qdrant = get_qdrant()
    async with AsyncSessionLocal() as session:
        for source in all_sources():
            rows = int((await session.execute(select(func.count()).select_from(source.model))).scalar_one())
            current = int((await session.execute(
                select(func.count()).select_from(source.model).where(source.model.embed_model == signature)
            )).scalar_one())
            exists = await asyncio.to_thread(qdrant.collection_exists, source.collection)
            points = None
            if exists:
                points = int((await asyncio.to_thread(qdrant.count, source.collection, exact=True)).count)
            result["sources"].append({
                "source": source.key, "rows": rows, "current_signature_rows": current,
                "collection_exists": exists, "qdrant_points": points,
                "ready": rows == current and (rows == 0 or bool(points and points > 0)),
            })
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
