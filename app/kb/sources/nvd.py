"""NVD CVE source adapter."""

from __future__ import annotations

import re

from app.kb.base import KBSource, RetrievalHit
from app.kb.models import NVDEntry


class NVDSource(KBSource):
    key = "nvd"
    display_name = "NVD CVE Feed"
    model = NVDEntry
    # Public reference data: authoritative for "does this CVE exist and how bad
    # is it", but less directly actionable than the firm's own write-ups, so it
    # carries the baseline fusion weight.
    weight = 1.0
    id_pattern = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)

    @property
    def pk_column(self):
        return NVDEntry.cve_id

    def build_text(self, row: NVDEntry) -> str:
        """
        The CVE ID is repeated at the front because analysts paste bare IDs as
        queries; keeping it in the embedded text (in addition to the exact-ID
        arm) means the dense arm also has a chance of matching it.

        Affected products matter enormously for red-team validation — "Apache
        Struts 2.5.30" in a finding must be able to match the CPE strings — so
        they are embedded rather than kept as filter-only metadata.
        """
        parts = [
            row.cve_id,
            row.description or "",
        ]
        if row.cwe:
            parts.append(f"Weakness: {row.cwe}")
        if row.severity or row.cvss_v3 is not None:
            parts.append(
                f"Severity: {row.severity or 'unknown'} CVSS {row.cvss_v3 if row.cvss_v3 is not None else 'n/a'}"
            )
        if row.attack_vector:
            parts.append(
                f"Attack vector: {row.attack_vector}, complexity {row.attack_complexity}, "
                f"privileges {row.privileges_required}, user interaction {row.user_interaction}"
            )
        products = row.affected_products or []
        if products:
            parts.append("Affected products: " + ", ".join(products[:20]))
        return "\n".join(p for p in parts if p)

    def build_payload(self, row: NVDEntry) -> dict:
        return {
            "source": self.key,
            "doc_id": row.cve_id,
            "title": row.cve_id,
            "description": row.description or "",
            "cvss_v3": row.cvss_v3 if row.cvss_v3 is not None else 0.0,
            "severity": row.severity or "",
            "cwe": row.cwe or "",
            "vuln_status": row.vuln_status or "",
            "attack_vector": row.attack_vector or "",
            "attack_complexity": row.attack_complexity or "",
            "privileges_required": row.privileges_required or "",
            "user_interaction": row.user_interaction or "",
            "exploitability": row.exploitability_score or 0.0,
            "impact_score": row.impact_score or 0.0,
            "affected_products": (row.affected_products or [])[:20],
            "ref_urls": (row.ref_urls or [])[:5],
            "published_date": row.published_date.isoformat() if row.published_date else "",
            "last_modified": row.last_modified.isoformat() if row.last_modified else "",
        }

    def hit_from_payload(self, payload: dict, score: float) -> RetrievalHit:
        refs = payload.get("ref_urls") or []
        return RetrievalHit(
            source_key=self.key,
            source_label=self.display_name,
            doc_id=payload.get("doc_id") or payload.get("kb_id") or "",
            title=payload.get("title") or payload.get("doc_id") or "",
            text=payload.get("description") or "",
            score=score,
            url=refs[0] if refs else None,
            payload={
                "cvss_v3": payload.get("cvss_v3"),
                "severity": payload.get("severity"),
                "cwe": payload.get("cwe"),
                "attack_vector": payload.get("attack_vector"),
                "affected_products": payload.get("affected_products") or [],
                "ref_urls": refs[:3],
                "published_date": payload.get("published_date"),
            },
        )
