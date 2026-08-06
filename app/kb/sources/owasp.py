"""OWASP Top 10 source adapter."""

from __future__ import annotations

import re

from app.kb.base import KBSource, RetrievalHit
from app.kb.models import OwaspTop10Entry


class OwaspSource(KBSource):
    key = "owasp"
    display_name = "OWASP Top 10"
    model = OwaspTop10Entry
    weight = 1.1
    id_pattern = re.compile(r"\bA(?:0[1-9]|10)(?::|_)?20\d{2}\b", re.IGNORECASE)

    @property
    def pk_column(self):
        return OwaspTop10Entry.category_id

    def normalize_id(self, raw: str) -> str:
        compact = raw.upper().replace("_", ":")
        return compact if ":" in compact else compact[:3] + ":" + compact[3:]

    def build_text(self, row: OwaspTop10Entry) -> str:
        parts = [
            f"{row.category_id} {row.name}",
            row.overview or "",
            row.description or "",
        ]
        if row.prevention:
            parts.append("How to prevent:\n" + row.prevention)
        if row.scenarios:
            parts.append("Example attack scenarios:\n" + row.scenarios)
        if row.cwe_ids:
            parts.append("Mapped weaknesses: " + ", ".join(row.cwe_ids))
        return "\n\n".join(part for part in parts if part)

    def build_payload(self, row: OwaspTop10Entry) -> dict:
        return {
            "source": self.key,
            "doc_id": row.category_id,
            "title": f"{row.category_id} - {row.name}",
            "description": row.overview or row.description or "",
            "rank": row.rank,
            "year": row.year,
            "cwe_ids": row.cwe_ids or [],
            "ref_urls": row.ref_urls or [],
            "source_url": row.source_url,
        }

    def hit_from_payload(self, payload: dict, score: float) -> RetrievalHit:
        source_url = payload.get("source_url")
        refs = payload.get("ref_urls") or []
        return RetrievalHit(
            source_key=self.key,
            source_label=self.display_name,
            doc_id=payload.get("doc_id") or "",
            title=payload.get("title") or payload.get("doc_id") or "",
            text=payload.get("description") or "",
            score=score,
            url=source_url or (refs[0] if refs else None),
            payload={
                "rank": payload.get("rank"),
                "year": payload.get("year"),
                "cwe_ids": payload.get("cwe_ids") or [],
                "ref_urls": refs,
            },
        )
