"""
MITRE ATT&CK sync → `mitre_techniques` table + `kb_mitre` Qdrant collection.

Deprecated and revoked techniques are stored rather
than dropped, with a `deprecated` flag.  They are filtered out of retrieval by
default, but keeping them means an analyst pasting an older technique ID still
gets an answer ("T1064 is deprecated, superseded by T1059") instead of silence.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.kb.indexer import delete_document, index_rows
from app.kb.models import MitreTechnique
from app.kb.registry import get_source
from app.services.retrieval import get_qdrant

log = logging.getLogger(__name__)


async def sync_mitre(session: AsyncSession) -> dict:
    source = get_source("mitre")
    qdrant = get_qdrant()

    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        resp = await client.get(settings.MITRE_STIX_URL)
        resp.raise_for_status()
        bundle = resp.json()

    attack_version = _bundle_version(bundle) or _release_version(str(resp.url))
    rows: list[MitreTechnique] = []

    for obj in bundle.get("objects", []):
        if obj.get("type") != "attack-pattern":
            continue

        technique_id = _external_id(obj)
        if not technique_id:
            continue

        deprecated = bool(obj.get("revoked") or obj.get("x_mitre_deprecated"))

        ref_urls = [
            r["url"] for r in obj.get("external_references", []) if r.get("url")
        ][:5]

        values = {
            "name": obj.get("name", "") or "",
            "description": obj.get("description", "") or "",
            "tactics": [
                p["phase_name"]
                for p in obj.get("kill_chain_phases", [])
                if p.get("phase_name")
            ],
            "platforms": obj.get("x_mitre_platforms", []) or [],
            "data_sources": obj.get("x_mitre_data_sources", []) or [],
            "detection": obj.get("x_mitre_detection", "") or "",
            "is_subtechnique": bool(obj.get("x_mitre_is_subtechnique", False)),
            "parent_technique_id": (
                technique_id.split(".")[0] if "." in technique_id else None
            ),
            "ref_urls": ref_urls,
            "attack_version": attack_version,
            "deprecated": deprecated,
        }

        row = await session.get(MitreTechnique, technique_id)
        if row is None:
            row = MitreTechnique(technique_id=technique_id, **values)
            session.add(row)
        else:
            for k, v in values.items():
                setattr(row, k, v)
        rows.append(row)

    # Postgres first, committed, before any vector write — same ordering
    # discipline as the NVD sync.
    await session.commit()
    log.info("MITRE: %d techniques persisted, indexing…", len(rows))

    stats = await index_rows(source, session, qdrant, rows)
    if stats.failed:
        raise RuntimeError(
            f"MITRE indexing failed for {stats.failed} techniques; stale data retained"
        )

    canonical_ids = [row.technique_id for row in rows]
    stale_ids = list(
        (
            await session.execute(
                select(MitreTechnique.technique_id).where(
                    MitreTechnique.technique_id.not_in(canonical_ids)
                )
            )
        ).scalars()
    )
    for technique_id in stale_ids:
        await delete_document(qdrant, source, technique_id)
    if stale_ids:
        await session.execute(
            delete(MitreTechnique).where(MitreTechnique.technique_id.in_(stale_ids))
        )
        await session.commit()

    log.info("MITRE sync complete: %s", stats)
    return {
        "attack_version": attack_version,
        "rows": len(rows),
        "removed_stale_rows": len(stale_ids),
        **stats.to_dict(),
    }


def _external_id(obj: dict) -> Optional[str]:
    for ref in obj.get("external_references", []):
        if ref.get("source_name") == "mitre-attack":
            return ref.get("external_id")
    return None


def _bundle_version(bundle: dict) -> Optional[str]:
    for obj in bundle.get("objects", []):
        if obj.get("type") == "x-mitre-collection":
            return obj.get("x_mitre_version")
    return None


def _release_version(url: str) -> Optional[str]:
    match = re.search(r"/download/v([^/]+)/", url)
    return match.group(1) if match else None
