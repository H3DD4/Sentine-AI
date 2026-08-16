"""Parse and index the firm's DOCX finding-template libraries."""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.finding_templates_text import clean_for_embedding
from app.kb.indexer import delete_document, index_rows
from app.kb.models import FindingTemplate
from app.kb.registry import get_source
from app.services.retrieval import get_qdrant

log = logging.getLogger(__name__)

PARSER_VERSION = "1"
DEFAULT_SOURCE_DIR = Path(__file__).resolve().parents[1] / "kb" / "sources"
CODE_RE = re.compile(r"^(?:(?:TII|TIS|ASIA)_)?(?:BP|V)_\d{3}$", re.IGNORECASE)
ISO_RE = re.compile(r"\bA\.\d+(?:\.\d+)+\b", re.IGNORECASE)


@dataclass
class ParsedTemplate:
    id: str
    template_code: str
    record_kind: str
    source_file: str
    source_file_hash: str
    source_table_index: int
    section: str = ""
    category: str = ""
    topic: str = ""
    title: str = ""
    iso_references: list[str] = field(default_factory=list)
    observations: str = ""
    evidence_template: str = ""
    affected_elements: str = ""
    impact: str = ""
    recommendation: str = ""
    implementation_complexity: str = ""
    implementation_priority: str = ""
    risk_assessments: list[dict] = field(default_factory=list)
    raw_fields: dict = field(default_factory=dict)
    parse_warnings: list[str] = field(default_factory=list)

    def as_model_data(self) -> dict:
        return {
            "id": self.id,
            "template_code": self.template_code,
            "record_kind": self.record_kind,
            "source_file": self.source_file,
            "source_file_hash": self.source_file_hash,
            "source_table_index": self.source_table_index,
            "section": self.section,
            "category": self.category,
            "topic": self.topic,
            "title": self.title,
            "iso_references": self.iso_references,
            "observations": self.observations,
            "evidence_template": self.evidence_template,
            "affected_elements": self.affected_elements,
            "impact": self.impact,
            "recommendation": self.recommendation,
            "implementation_complexity": self.implementation_complexity,
            "implementation_priority": self.implementation_priority,
            "risk_assessments": self.risk_assessments,
            "raw_fields": self.raw_fields,
            "parse_warnings": self.parse_warnings,
            "parser_version": PARSER_VERSION,
        }


def _norm_label(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("’", "'").replace("`", "'")
    value = re.sub(r"[^a-z0-9]+", " ", value).strip()
    # One source table contains the export typo "0Elements impactes".
    value = re.sub(r"^0(?=elements impactes\b)", "", value).strip()
    return value


_LABELS = {
    "references": "references",
    "reference": "references",
    "constats": "observations",
    "preuves": "evidence_template",
    "elements impactes": "affected_elements",
    "impacts": "impact",
    "niveau d impact": "impact_level",
    "probabilite de survenance": "likelihood",
    "niveau de criticite": "criticality",
    "type": "finding_type",
    "recommandation": "recommendation",
    "complexite de mise en oeuvre": "implementation_complexity",
    "priorite de mise en oeuvre": "implementation_priority",
}


def _clean_cell(value: str) -> str:
    value = (value or "").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _logical_rows(table) -> list[list[str]]:
    """Read each underlying XML cell once so merged cells do not duplicate text."""
    rows: list[list[str]] = []
    for row in table.rows:
        cells: list[str] = []
        seen: set[int] = set()
        for cell in row.cells:
            marker = id(cell._tc)
            if marker in seen:
                continue
            seen.add(marker)
            cells.append(_clean_cell(cell.text))
        rows.append(cells)
    return rows


def _body_context(document) -> dict[object, tuple[str, str, str]]:
    """Map table object identity to the headings active immediately before it."""
    context: dict[object, tuple[str, str, str]] = {}
    section = category = topic = ""
    for element in document.element.body.iterchildren():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(element, document)
            if paragraph.text.strip():
                style = (paragraph.style.name or "").lower()
                text = _clean_cell(paragraph.text)
                if "heading 1" in style:
                    section, category, topic = text, "", ""
                elif "heading 2" in style:
                    category, topic = text, ""
                elif "heading 3" in style:
                    topic = text
        elif tag == "tbl":
            context[element] = (section, category, topic)
    return context


def _record_code(rows: list[list[str]]) -> str:
    if not rows or not rows[0]:
        return ""
    for cell in rows[0][:2]:
        candidate = re.sub(r"\s+", "", cell).upper()
        if CODE_RE.fullmatch(candidate):
            return candidate
    return ""


def _risk_condition(label: str) -> str:
    normalized = _norm_label(label)
    if "sans prendre en consideration" in normalized or "sans waf" in normalized:
        return "without_waf"
    if "prenant en consideration" in normalized or "avec waf" in normalized:
        return "with_waf"
    return "default"


def _parse_fields(rows: list[list[str]]) -> tuple[dict[str, list[str]], list[dict], list[str]]:
    fields: dict[str, list[str]] = {}
    risks: list[dict] = []
    warnings: list[str] = []
    active_risk = "default"
    current_risk: Optional[dict] = None

    for row in rows[1:]:
        values = [value for value in row if value]
        if not values:
            continue
        normalized_values = [_norm_label(value) for value in values]
        conditional = next(
            (value for value in values if "estimation du niveau de risque" in _norm_label(value)),
            None,
        )
        if conditional:
            active_risk = _risk_condition(conditional)
            current_risk = {"condition": active_risk}
            risks.append(current_risk)
            continue

        matched = False
        # Most rows are label/value pairs, while four-column rows contain two
        # pairs: impact-level/value + likelihood/value, or criticality/value +
        # type/value. Parse every pair instead of letting the first label absorb
        # the second label and its value.
        for index, label in enumerate(normalized_values):
            if label not in _LABELS:
                continue
            matched = True
            value = values[index + 1] if index + 1 < len(values) else ""
            if not value:
                continue
            canonical = _LABELS[label]
            if canonical in {"impact_level", "likelihood", "criticality", "finding_type"}:
                if current_risk is None or current_risk.get("condition") != active_risk:
                    current_risk = {"condition": active_risk}
                    risks.append(current_risk)
                current_risk[canonical] = value
            else:
                fields.setdefault(canonical, []).append(value)
        if not matched and len(values) > 1 and any(
            "niveau de risque" in value for value in normalized_values
        ):
            warnings.append(f"unparsed risk row: {values[0][:120]}")

    # Only retain non-default risk objects when they contain actual values.
    risks = [
        risk for risk in risks
        if any(key in risk for key in ("impact_level", "likelihood", "criticality", "finding_type"))
    ]
    return fields, risks, warnings


def _join(fields: dict[str, list[str]], key: str) -> str:
    return "\n".join(fields.get(key, [])).strip()


def _stable_id(source_file: str, table_index: int, code: str) -> str:
    identity = f"{source_file}\n{table_index}\n{code}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:32]


def parse_docx(path: Path) -> list[ParsedTemplate]:
    path = Path(path)
    raw = path.read_bytes()
    file_hash = hashlib.sha256(raw).hexdigest()
    document = Document(str(path))
    contexts = _body_context(document)
    parsed: list[ParsedTemplate] = []

    for table_index, table in enumerate(document.tables):
        rows = _logical_rows(table)
        code = _record_code(rows)
        if not code:
            continue
        title = rows[0][1] if rows and len(rows[0]) > 1 else ""
        fields, risks, warnings = _parse_fields(rows)
        section, category, topic = contexts.get(table._tbl, ("", "", ""))
        record_kind = "positive_practice" if "BP_" in code else "vulnerability"
        references_text = _join(fields, "references")
        iso_refs = sorted(set(value.upper() for value in ISO_RE.findall(references_text)))
        raw_fields = {key: values for key, values in fields.items()}
        parsed.append(
            ParsedTemplate(
                id=_stable_id(path.name, table_index, code),
                template_code=code,
                record_kind=record_kind,
                source_file=path.name,
                source_file_hash=file_hash,
                source_table_index=table_index,
                section=section,
                category=category,
                topic=topic,
                title=title,
                iso_references=iso_refs,
                observations=_join(fields, "observations"),
                evidence_template=_join(fields, "evidence_template"),
                affected_elements=_join(fields, "affected_elements"),
                impact=_join(fields, "impact"),
                recommendation=_join(fields, "recommendation"),
                implementation_complexity=_join(fields, "implementation_complexity"),
                implementation_priority=_join(fields, "implementation_priority"),
                risk_assessments=risks,
                raw_fields=raw_fields,
                parse_warnings=warnings,
            )
        )
    return parsed


async def sync_finding_templates(
    session: AsyncSession,
    paths: Optional[Iterable[Path | str]] = None,
) -> dict:
    """Import all supplied DOCX cards, index them, and remove stale cards."""
    source = get_source("finding_templates")
    qdrant = get_qdrant()
    paths = list(paths) if paths is not None else sorted(DEFAULT_SOURCE_DIR.glob("*.docx"))
    if not paths:
        raise RuntimeError(f"No DOCX finding-template sources found in {DEFAULT_SOURCE_DIR}")

    parsed: list[ParsedTemplate] = []
    for path in paths:
        path = Path(path)
        if path.suffix.lower() != ".docx":
            raise ValueError(f"Finding-template source is not a DOCX: {path}")
        parsed.extend(parse_docx(path))
    if not parsed:
        raise RuntimeError("No structured finding-template tables were found")

    rows: list[FindingTemplate] = []
    for item in parsed:
        row = await session.get(FindingTemplate, item.id)
        data = item.as_model_data()
        if row is None:
            row = FindingTemplate(**data)
            session.add(row)
        else:
            for key, value in data.items():
                setattr(row, key, value)
        rows.append(row)
    await session.commit()

    stats = await index_rows(source, session, qdrant, rows, batch_size=32)
    if stats.failed:
        raise RuntimeError(f"Finding-template indexing failed for {stats.failed} documents")

    canonical_ids = {row.id for row in rows}
    source_files = {Path(path).name for path in paths}
    stale_ids = list(
        (
            await session.execute(
                select(FindingTemplate.id).where(
                    FindingTemplate.source_file.in_(source_files),
                    FindingTemplate.id.not_in(canonical_ids),
                )
            )
        ).scalars()
    )
    for document_id in stale_ids:
        await delete_document(qdrant, source, document_id)
    if stale_ids:
        await session.execute(delete(FindingTemplate).where(FindingTemplate.id.in_(stale_ids)))
        await session.commit()

    return {
        "files": len(paths),
        "rows": len(rows),
        "positive_practices": sum(row.record_kind == "positive_practice" for row in rows),
        "vulnerabilities": sum(row.record_kind == "vulnerability" for row in rows),
        "removed_stale_rows": len(stale_ids),
        **stats.to_dict(),
    }
