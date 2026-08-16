"""Firm-authored finding and positive-practice template source adapter."""

from __future__ import annotations

import re

from app.kb.base import KBSource, RetrievalHit
from app.ingestion.finding_templates_text import clean_for_embedding
from app.kb.models import FindingTemplate


class FindingTemplatesSource(KBSource):
    key = "finding_templates"
    display_name = "Internal finding templates"
    model = FindingTemplate
    # These are approved firm templates and highly actionable, but they are
    # examples rather than evidence that a current target is vulnerable.
    weight = 1.2
    id_pattern = re.compile(
        r"\b(?:(?:TII|TIS|ASIA)_)?(?:BP|V)_\d{3}\b", re.IGNORECASE
    )

    @property
    def pk_column(self):
        return FindingTemplate.id

    @property
    def exact_id_payload_field(self) -> str:
        return "template_code"

    def build_text(self, row: FindingTemplate) -> str:
        parts = [
            "Document type: Internal finding template",
            f"Record kind: {row.record_kind}",
            f"Template ID: {row.template_code}",
            f"Title: {clean_for_embedding(row.title)}",
        ]
        for label, value in (
            ("Scope", row.category),
            ("Topic", row.topic),
            ("ISO 27001 references", ", ".join(row.iso_references or [])),
            ("Observation", row.observations),
            ("Evidence pattern", row.evidence_template),
            ("Affected elements", row.affected_elements),
            ("Impact", row.impact),
            ("Recommendation", row.recommendation),
            ("Implementation complexity", row.implementation_complexity),
            ("Implementation priority", row.implementation_priority),
        ):
            if value:
                parts.append(f"{label}: {clean_for_embedding(str(value))}")
        for risk in row.risk_assessments or []:
            condition = risk.get("condition") or "default"
            details = ", ".join(
                f"{key.replace('_', ' ')}: {risk[key]}"
                for key in ("impact_level", "likelihood", "criticality", "finding_type")
                if risk.get(key)
            )
            if details:
                parts.append(f"Risk assessment ({condition}): {details}")
        return "\n".join(parts)

    def build_payload(self, row: FindingTemplate) -> dict:
        return {
            "source": self.key,
            "doc_id": row.id,
            "template_code": row.template_code,
            "title": row.title or "",
            "description": row.observations or "",
            "record_kind": row.record_kind,
            "source_file": row.source_file,
            "source_table_index": row.source_table_index,
            "category": row.category or "",
            "topic": row.topic or "",
            "iso_references": row.iso_references or [],
            "evidence_template": row.evidence_template or "",
            "affected_elements": row.affected_elements or "",
            "impact": row.impact or "",
            "recommendation": row.recommendation or "",
            "implementation_complexity": row.implementation_complexity or "",
            "implementation_priority": row.implementation_priority or "",
            "risk_assessments": row.risk_assessments or [],
        }

    def hit_from_payload(self, payload: dict, score: float) -> RetrievalHit:
        return RetrievalHit(
            source_key=self.key,
            source_label=self.display_name,
            doc_id=payload.get("doc_id") or "",
            title=payload.get("title") or payload.get("template_code") or "",
            text=payload.get("description") or "",
            score=score,
            payload={
                "template_code": payload.get("template_code") or "",
                "record_kind": payload.get("record_kind") or "",
                "category": payload.get("category") or "",
                "topic": payload.get("topic") or "",
                "iso_references": payload.get("iso_references") or [],
                "evidence_template": payload.get("evidence_template") or "",
                "affected_elements": payload.get("affected_elements") or "",
                "impact": payload.get("impact") or "",
                "recommendation": payload.get("recommendation") or "",
                "risk_assessments": payload.get("risk_assessments") or [],
                "source_file": payload.get("source_file") or "",
                "source_table_index": payload.get("source_table_index"),
            },
        )
