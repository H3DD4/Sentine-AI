"""
NVD CVE sync → `nvd_entries` table + `kb_nvd` Qdrant collection.

Fixes the defect that produced the 10,459-vectors / 0-rows split-brain:
the previous version upserted a vector for every CVE *inside* the pagination
loop but called `session.commit()` only once, after the whole loop had
finished.  Qdrant writes are not transactional and SQLAlchemy buffers until
commit, so when the sync died mid-run — which the 6-second unauthenticated
rate-limit sleep makes likely on a long backfill — every vector survived and
every row was rolled back.

The rewrite separates the two concerns:

    fetch page → upsert rows → COMMIT → index rows (embed + vector upsert,
    then stamp qdrant_synced_at and commit again)

Postgres is the system of record and is always committed first.  Vectors are
written afterwards by the shared indexer, which marks each row synced only
after its upsert succeeds.  A crash now leaves rows with `qdrant_synced_at IS
NULL`, which is both recoverable (re-run picks them up) and visible to the
health check as drift, instead of an invisible inconsistency.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.kb.indexer import index_rows
from app.kb.models import NVDEntry
from app.kb.registry import get_source
from app.services.retrieval import get_qdrant

log = logging.getLogger(__name__)

NVD_PAGE_SIZE = 2000


async def sync_nvd(session: AsyncSession, days_back: int = 2) -> dict:
    """Pull CVEs modified in the last N days into Postgres, then index them."""
    source = get_source("nvd")
    qdrant = get_qdrant()

    end = datetime.utcnow()
    start = end - timedelta(days=days_back)

    base_params = {
        "lastModStartDate": start.strftime("%Y-%m-%dT%H:%M:%S.000"),
        "lastModEndDate": end.strftime("%Y-%m-%dT%H:%M:%S.000"),
        "resultsPerPage": NVD_PAGE_SIZE,
    }
    headers = {}
    if settings.NVD_API_KEY:
        headers["apiKey"] = settings.NVD_API_KEY

    total_rows = 0
    total_indexed = 0
    pages = 0

    async with httpx.AsyncClient(timeout=90) as http:
        start_index = 0
        while True:
            params = {**base_params, "startIndex": start_index}
            resp = await http.get(settings.NVD_BASE_URL, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            vulnerabilities = data.get("vulnerabilities", [])
            total_results = data.get("totalResults", 0)
            if not vulnerabilities:
                break

            log.info(
                "NVD page startIndex=%d: %d/%d CVEs",
                start_index,
                start_index + len(vulnerabilities),
                total_results,
            )

            # 1. Persist the page and COMMIT before touching Qdrant. If the
            #    process dies after this point the data is safe on disk.
            rows = []
            for item in vulnerabilities:
                row = await _upsert_cve(item, session)
                if row is not None:
                    rows.append(row)
            await session.commit()
            total_rows += len(rows)

            # 2. Index the page. Unchanged CVEs are skipped by content hash, so
            #    re-running a sync over overlapping windows is cheap.
            stats = await index_rows(source, session, qdrant, rows)
            total_indexed += stats.indexed

            pages += 1
            start_index += len(vulnerabilities)
            if start_index >= total_results:
                break

            # NVD rate limit: ~5 req/30s without a key, ~50 req/30s with one.
            await asyncio.sleep(0.6 if settings.NVD_API_KEY else 6.0)

    log.info(
        "NVD sync complete: %d rows persisted, %d indexed, %d pages",
        total_rows,
        total_indexed,
        pages,
    )
    return {"rows": total_rows, "indexed": total_indexed, "pages": pages}


async def _upsert_cve(item: dict, session: AsyncSession) -> Optional[NVDEntry]:
    cve = item.get("cve") or {}
    cve_id = cve.get("id")
    if not cve_id:
        return None

    description = next(
        (d["value"] for d in cve.get("descriptions", []) if d.get("lang") == "en"), ""
    )

    fields = _parse_cvss(cve.get("metrics", {}))

    # CWE
    cwe = None
    for weakness in cve.get("weaknesses", []):
        for desc in weakness.get("description", []):
            value = desc.get("value", "")
            if value.startswith("CWE-"):
                cwe = value
                break
        if cwe:
            break

    # Affected products (CPE)
    affected = []
    for config in cve.get("configurations", []):
        for node in config.get("nodes", []):
            for match in node.get("cpeMatch", []):
                parts = (match.get("criteria") or "").split(":")
                if len(parts) > 4:
                    affected.append(f"{parts[3]} {parts[4]}")

    ref_urls = [r["url"] for r in cve.get("references", []) if r.get("url")][:5]

    values = {
        "description": description,
        "vuln_status": cve.get("vulnStatus", ""),
        "cwe": cwe,
        "affected_products": sorted(set(affected))[:20],
        "ref_urls": ref_urls,
        "published_date": _parse_dt(cve.get("published", "")),
        "last_modified": _parse_dt(cve.get("lastModified", "")),
        **fields,
    }

    row = await session.get(NVDEntry, cve_id)
    if row is None:
        row = NVDEntry(cve_id=cve_id, **values)
        session.add(row)
    else:
        for k, v in values.items():
            setattr(row, k, v)
    return row


def _parse_cvss(metrics: dict) -> dict:
    """Extract the CVSS v3.1 breakdown, falling back to v3.0."""
    out = {
        "cvss_v3": None,
        "severity": None,
        "exploitability_score": None,
        "impact_score": None,
        "attack_vector": None,
        "attack_complexity": None,
        "privileges_required": None,
        "user_interaction": None,
        "confidentiality_impact": None,
        "integrity_impact": None,
        "availability_impact": None,
    }
    for key in ("cvssMetricV31", "cvssMetricV30"):
        entries = metrics.get(key, [])
        if not entries:
            continue
        d = entries[0].get("cvssData", {})
        out.update(
            {
                "cvss_v3": d.get("baseScore"),
                "severity": d.get("baseSeverity"),
                "exploitability_score": entries[0].get("exploitabilityScore"),
                "impact_score": entries[0].get("impactScore"),
                "attack_vector": d.get("attackVector"),
                "attack_complexity": d.get("attackComplexity"),
                "privileges_required": d.get("privilegesRequired"),
                "user_interaction": d.get("userInteraction"),
                "confidentiality_impact": d.get("confidentialityImpact"),
                "integrity_impact": d.get("integrityImpact"),
                "availability_impact": d.get("availabilityImpact"),
            }
        )
        break
    return out


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except ValueError:
        return None
