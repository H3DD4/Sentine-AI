"""
Backfill: recover the legacy `kb_entries` Qdrant collection into Postgres.

Why this script exists
----------------------
The previous NVD sync wrote vectors to Qdrant inside its pagination loop but
committed Postgres rows only after the entire loop finished.  The run died
partway through, so the vectors persisted (10,459 points) and every row was
rolled back — leaving retrieval permanently empty, because `hybrid_search`
looked up Qdrant hits in a table that had no matching rows.

The Qdrant payloads happen to carry every field the `nvd_entries` table needs,
so the corpus can be reconstructed from them directly.  That matters: without
an NVD API key the feed is rate-limited to roughly one page per six seconds,
so re-downloading would take hours, while this runs against local data.

Why vectors are re-embedded rather than copied
----------------------------------------------
The legacy vectors are not reusable even though they are present:

  * they were produced by mean-pooling 400-character chunks, which for a
    multi-sentence CVE description yields a centroid that represents none of
    the individual passages;
  * they carry no sparse (BM25) component, so hybrid search would have no
    lexical arm — the arm that matters most for version strings and CPEs;
  * they were embedded without the current chunking configuration, so their
    embed signature would immediately register as drift.

Copying them would reproduce the retrieval quality problem this migration
exists to fix, so the script re-embeds from the recovered text.

Usage
-----
    python -m scripts.backfill_nvd_from_qdrant --dry-run
    python -m scripts.backfill_nvd_from_qdrant
    python -m scripts.backfill_nvd_from_qdrant --rows-only   # skip re-embedding

Safe to re-run: rows are upserted by CVE ID and the indexer skips documents
whose content hash is unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from typing import Iterator, Optional

from qdrant_client import QdrantClient

from app.config import settings
from app.db import get_session_context
from app.kb.indexer import reindex_source
from app.kb.models import NVDEntry
from app.kb.registry import get_source
from app.services.retrieval import get_qdrant

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s"
)
# The Qdrant client logs one INFO line per HTTP call; at ~20 scroll pages plus
# one upsert per indexed batch that buries the progress output.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("backfill")

LEGACY_COLLECTION = settings.QDRANT_COLLECTION  # "kb_entries"
SCROLL_BATCH = 500


def _parse_dt(s) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s).replace("Z", ""))
    except ValueError:
        return None


def scroll_legacy(
    qdrant: QdrantClient, collection: str, entry_type: str = "cve"
) -> Iterator[dict]:
    """Stream payloads out of the legacy collection without loading them all."""
    offset = None
    while True:
        points, offset = qdrant.scroll(
            collection_name=collection,
            limit=SCROLL_BATCH,
            offset=offset,
            with_payload=True,
            with_vectors=False,  # legacy vectors are discarded, see module docstring
        )
        if not points:
            break
        for p in points:
            payload = p.payload or {}
            if entry_type and payload.get("type") != entry_type:
                continue
            yield payload
        if offset is None:
            break


def payload_to_values(p: dict) -> Optional[tuple[str, dict]]:
    cve_id = p.get("kb_id") or p.get("doc_id")
    if not cve_id or not str(cve_id).upper().startswith("CVE-"):
        return None

    def clean(v):
        """Legacy code coerced missing values to '' / 0.0; restore real NULLs so
        'unknown' is distinguishable from 'genuinely zero'."""
        return v if v not in ("", 0.0, 0) else None

    values = {
        "description": p.get("description") or "",
        "vuln_status": clean(p.get("vuln_status")),
        "cvss_v3": clean(p.get("cvss_v3")),
        "severity": clean(p.get("severity")),
        "exploitability_score": clean(p.get("exploitability")),
        "impact_score": clean(p.get("impact_score")),
        "attack_vector": clean(p.get("attack_vector")),
        "attack_complexity": clean(p.get("attack_complexity")),
        "privileges_required": clean(p.get("privileges_required")),
        "user_interaction": clean(p.get("user_interaction")),
        # Legacy payload used the bare CVSS sub-metric names; the table
        # disambiguates them with an `_impact` suffix.
        "confidentiality_impact": clean(p.get("confidentiality")),
        "integrity_impact": clean(p.get("integrity")),
        "availability_impact": clean(p.get("availability")),
        "cwe": clean(p.get("cwe")),
        "affected_products": p.get("affected_products") or [],
        # Renamed from `references`, which is a reserved word in Postgres.
        "ref_urls": list(dict.fromkeys(p.get("references") or []))[:5],
        "published_date": _parse_dt(p.get("published_date")),
        "last_modified": _parse_dt(p.get("last_modified")),
    }
    return str(cve_id).upper(), values


async def backfill_rows(dry_run: bool = False) -> tuple[int, int]:
    """Phase 1 — reconstruct Postgres rows from Qdrant payloads."""
    qdrant = get_qdrant()

    if not qdrant.collection_exists(LEGACY_COLLECTION):
        log.error("Legacy collection '%s' does not exist — nothing to backfill.", LEGACY_COLLECTION)
        return 0, 0

    total = qdrant.count(LEGACY_COLLECTION, exact=True).count
    log.info("Legacy collection '%s' holds %d points.", LEGACY_COLLECTION, total)

    seen = 0
    written = 0
    skipped = 0

    async with get_session_context() as session:
        batch = 0
        for payload in scroll_legacy(qdrant, LEGACY_COLLECTION):
            seen += 1
            parsed = payload_to_values(payload)
            if parsed is None:
                skipped += 1
                continue
            cve_id, values = parsed

            if dry_run:
                if written < 3:
                    log.info("[dry-run] %s → %s", cve_id, {k: v for k, v in list(values.items())[:6]})
                written += 1
                continue

            row = await session.get(NVDEntry, cve_id)
            if row is None:
                session.add(NVDEntry(cve_id=cve_id, **values))
            else:
                for k, v in values.items():
                    setattr(row, k, v)
            written += 1
            batch += 1

            if batch >= 500:
                await session.commit()
                batch = 0
                log.info("  committed %d/%d rows…", written, total)

        if not dry_run:
            await session.commit()

    log.info(
        "Phase 1 complete: %d points scanned, %d rows %s, %d skipped (non-CVE).",
        seen, written, "would be written" if dry_run else "written", skipped,
    )
    return written, skipped


async def backfill_vectors(force: bool = False) -> None:
    """Phase 2 — re-embed the recovered rows into the `kb_nvd` collection."""
    source = get_source("nvd")
    qdrant = get_qdrant()

    log.info(
        "Phase 2: re-embedding into '%s' (dense + BM25 sparse). "
        "This is CPU-bound and will take a while for a large corpus.",
        source.collection,
    )
    async with get_session_context() as session:
        stats = await reindex_source(source, session, qdrant, force=force)
    log.info("Phase 2 complete: %s", stats)


async def verify() -> None:
    """Report the final state of both stores so drift is visible immediately."""
    from app.kb.registry import check_all_sources

    qdrant = get_qdrant()
    async with get_session_context() as session:
        health = await check_all_sources(session, qdrant)

    log.info("── Final state ──────────────────────────────────────────")
    for key, h in health.items():
        log.info(
            "  %-12s %-12s rows=%-7d vectors=%-7d  %s",
            key, h.availability.value, h.row_count, h.vector_count, h.detail,
        )


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Parse only; write nothing")
    ap.add_argument("--rows-only", action="store_true", help="Phase 1 only, skip re-embedding")
    ap.add_argument("--vectors-only", action="store_true", help="Phase 2 only")
    ap.add_argument("--force", action="store_true", help="Re-embed even if unchanged")
    args = ap.parse_args()

    if not args.vectors_only:
        written, _ = await backfill_rows(dry_run=args.dry_run)
        if args.dry_run:
            log.info("Dry run — no changes made. Re-run without --dry-run to apply.")
            return 0
        if written == 0:
            log.warning("No rows recovered; skipping re-embedding.")
            return 1

    if not args.rows_only:
        await backfill_vectors(force=args.force)

    await verify()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
