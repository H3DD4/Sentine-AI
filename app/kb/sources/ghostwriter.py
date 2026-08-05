"""
Ghostwriter historical-findings source adapter.

This is the firm's own prior work: how Forvis Mazars has actually written up
this class of finding before, with real replication steps and real mitigations.
For finding validation it is the single most useful corpus, which is why it
carries the highest fusion weight.
"""

from __future__ import annotations

from app.kb.base import KBSource, RetrievalHit
from app.kb.models import GhostwriterFinding


class GhostwriterSource(KBSource):
    key = "ghostwriter"
    display_name = "Ghostwriter (firm findings)"
    model = GhostwriterFinding
    # Outranks the public feeds at equal retrieval rank: a prior firm write-up
    # is directly reusable, whereas a CVE record still needs interpretation.
    weight = 1.25
    # Ghostwriter findings have no canonical public identifier analysts would
    # paste, so there is no exact-ID arm for this source.
    id_pattern = None

    @property
    def pk_column(self):
        return GhostwriterFinding.id

    def build_text(self, row: GhostwriterFinding) -> str:
        """
        Replication steps and impact are embedded, not just stored.  An analyst
        describing what they did in a test should match the *steps* of a prior
        finding, which is often worded quite differently from its title.

        Client name is deliberately NOT embedded: it adds no retrieval signal
        and would let a client's name skew semantic similarity between
        otherwise-unrelated findings.
        """
        parts = [
            row.title or "",
            row.description or "",
        ]
        if row.finding_type:
            parts.append(f"Finding type: {row.finding_type}")
        if row.severity:
            parts.append(
                f"Severity: {row.severity}"
                + (f" (CVSS {row.cvss_score})" if row.cvss_score is not None else "")
            )
        if row.impact:
            parts.append(f"Impact: {row.impact}")
        if row.replication_steps:
            parts.append(f"Replication steps: {row.replication_steps}")
        if row.mitigation:
            parts.append(f"Mitigation: {row.mitigation}")
        techniques = row.mitre_techniques or []
        if techniques:
            parts.append("MITRE techniques: " + ", ".join(techniques))
        cves = row.cve_refs or []
        if cves:
            parts.append("Related CVEs: " + ", ".join(cves))
        tags = row.tags or []
        if tags:
            parts.append("Tags: " + ", ".join(tags))
        return "\n".join(p for p in parts if p)

    def build_payload(self, row: GhostwriterFinding) -> dict:
        return {
            "source": self.key,
            "doc_id": row.id,
            "gw_id": row.gw_id or "",
            "title": row.title or "",
            "description": row.description or "",
            "severity": row.severity or "",
            "cvss_score": row.cvss_score or 0.0,
            "finding_type": row.finding_type or "",
            "replication_steps": row.replication_steps or "",
            "mitigation": row.mitigation or "",
            "impact": row.impact or "",
            "mitre_techniques": row.mitre_techniques or [],
            "cve_refs": row.cve_refs or [],
            "tags": row.tags or [],
            "affected_entities": (row.affected_entities or [])[:20],
            "engagement_code": row.engagement_code or "",
            "client_name": row.client_name or "",
            "project_id": row.project_id or "",
            "gw_updated_at": row.gw_updated_at.isoformat() if row.gw_updated_at else "",
        }

    def hit_from_payload(self, payload: dict, score: float) -> RetrievalHit:
        return RetrievalHit(
            source_key=self.key,
            source_label=self.display_name,
            doc_id=payload.get("doc_id") or "",
            title=payload.get("title") or "(untitled finding)",
            text=payload.get("description") or "",
            score=score,
            url=None,
            payload={
                "severity": payload.get("severity"),
                "cvss_score": payload.get("cvss_score"),
                "finding_type": payload.get("finding_type"),
                "replication_steps": payload.get("replication_steps") or "",
                "mitigation": payload.get("mitigation") or "",
                "impact": payload.get("impact") or "",
                "mitre_techniques": payload.get("mitre_techniques") or [],
                "cve_refs": payload.get("cve_refs") or [],
                "engagement_code": payload.get("engagement_code") or "",
                "client_name": payload.get("client_name") or "",
            },
        )
