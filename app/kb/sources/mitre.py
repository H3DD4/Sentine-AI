"""MITRE ATT&CK technique source adapter."""

from __future__ import annotations

import re

from app.kb.base import KBSource, RetrievalHit
from app.kb.models import MitreTechnique


class MitreSource(KBSource):
    key = "mitre"
    display_name = "MITRE ATT&CK"
    model = MitreTechnique
    weight = 1.0
    # Matches T1059 and T1059.001 but not arbitrary T-prefixed words.
    id_pattern = re.compile(r"\bT\d{4}(?:\.\d{3})?\b")

    @property
    def pk_column(self):
        return MitreTechnique.technique_id

    def build_text(self, row: MitreTechnique) -> str:
        """
        Detection guidance is embedded because the most common analyst question
        of this corpus is "how would this be detected / what proves it happened"
        — that text is the answer, so it must be searchable, not just displayed.
        """
        parts = [
            f"{row.technique_id} {row.name}",
            f"ATT&CK version: {row.attack_version}" if row.attack_version else "",
            row.description or "",
        ]
        tactics = row.tactics or []
        if tactics:
            parts.append("Tactics: " + ", ".join(tactics))
        platforms = row.platforms or []
        if platforms:
            parts.append("Platforms: " + ", ".join(platforms))
        if row.detection:
            parts.append(f"Detection: {row.detection}")
        data_sources = row.data_sources or []
        if data_sources:
            parts.append("Data sources: " + ", ".join(data_sources))
        return "\n".join(p for p in parts if p)

    def build_payload(self, row: MitreTechnique) -> dict:
        return {
            "source": self.key,
            "doc_id": row.technique_id,
            "title": f"{row.technique_id} — {row.name}",
            "name": row.name or "",
            "description": row.description or "",
            "tactics": row.tactics or [],
            "platforms": row.platforms or [],
            "data_sources": row.data_sources or [],
            "detection": row.detection or "",
            "is_subtechnique": bool(row.is_subtechnique),
            "parent_technique_id": row.parent_technique_id or "",
            "ref_urls": (row.ref_urls or [])[:5],
            "attack_version": row.attack_version,
            "deprecated": bool(row.deprecated),
        }

    def hit_from_payload(self, payload: dict, score: float) -> RetrievalHit:
        tid = payload.get("doc_id") or ""
        refs = payload.get("ref_urls") or []
        url = refs[0] if refs else (
            f"https://attack.mitre.org/techniques/{tid.replace('.', '/')}/" if tid else None
        )
        return RetrievalHit(
            source_key=self.key,
            source_label=self.display_name,
            doc_id=tid,
            title=payload.get("title") or tid,
            text=payload.get("description") or "",
            score=score,
            url=url,
            payload={
                "tactics": payload.get("tactics") or [],
                "platforms": payload.get("platforms") or [],
                "detection": payload.get("detection") or "",
                "data_sources": payload.get("data_sources") or [],
                "attack_version": payload.get("attack_version"),
                "mitre_techniques": [tid] if tid else [],
            },
        )
