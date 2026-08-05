"""Internal analyst knowledge source adapter (playbooks, checklists, notes)."""

from __future__ import annotations

from app.kb.base import KBSource, RetrievalHit
from app.kb.models import InternalDoc


class InternalSource(KBSource):
    key = "internal"
    display_name = "Internal playbooks"
    model = InternalDoc
    # Curated by the team, so more trustworthy than a public feed, but narrower
    # in coverage than Ghostwriter's real engagement history.
    weight = 1.1
    id_pattern = None

    @property
    def pk_column(self):
        return InternalDoc.id

    def build_text(self, row: InternalDoc) -> str:
        parts = [row.title or "", row.body or ""]
        steps = row.validation_steps or []
        if steps:
            parts.append("Validation steps: " + " ".join(steps))
        indicators = row.indicators or []
        if indicators:
            parts.append("Indicators: " + ", ".join(indicators))
        techniques = row.mitre_techniques or []
        if techniques:
            parts.append("MITRE techniques: " + ", ".join(techniques))
        tags = row.tags or []
        if tags:
            parts.append("Tags: " + ", ".join(tags))
        return "\n".join(p for p in parts if p)

    def build_payload(self, row: InternalDoc) -> dict:
        return {
            "source": self.key,
            "doc_id": row.id,
            "title": row.title or "",
            "description": row.body or "",
            "doc_type": row.doc_type or "note",
            "tags": row.tags or [],
            "mitre_techniques": row.mitre_techniques or [],
            "validation_steps": row.validation_steps or [],
            "indicators": row.indicators or [],
            "ref_urls": (row.ref_urls or [])[:5],
            "author": row.author or "",
        }

    def hit_from_payload(self, payload: dict, score: float) -> RetrievalHit:
        refs = payload.get("ref_urls") or []
        return RetrievalHit(
            source_key=self.key,
            source_label=self.display_name,
            doc_id=payload.get("doc_id") or "",
            title=payload.get("title") or "(untitled)",
            text=payload.get("description") or "",
            score=score,
            url=refs[0] if refs else None,
            payload={
                "doc_type": payload.get("doc_type"),
                "tags": payload.get("tags") or [],
                "validation_steps": payload.get("validation_steps") or [],
                "indicators": payload.get("indicators") or [],
                "mitre_techniques": payload.get("mitre_techniques") or [],
                "author": payload.get("author") or "",
            },
        )
