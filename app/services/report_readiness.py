"""Structured conversation extraction and deterministic report-readiness scoring."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from app.models import Finding, VerdictEnum as FindingVerdict
from app.schemas import (
    ChatMessage,
    ReadinessDimension,
    ReportDraft,
    ReportReadinessResponse,
)
from app.services.llm_client import AsyncLLMClient


READINESS_THRESHOLD = 3.5
log = logging.getLogger(__name__)
_SOCIAL_TURNS = {
    "hello", "hi", "hey", "good morning", "good afternoon", "good evening",
    "thanks", "thank you", "ok", "okay",
}

EXTRACTION_SYSTEM = """You extract a penetration-testing report draft from a conversation.
Use only facts explicitly stated by the user or present in a validation result in the conversation.
Never invent a target, reproduction step, impact, CVE, ATT&CK technique, verdict, or remediation.
An assistant question is not evidence and must not be copied as an answer.

Return only a valid JSON object with this schema:
{
  "title": "",
  "affected_scope": "",
  "description": "",
  "technical_evidence": "",
  "reproduction_steps": [],
  "impact": "",
  "severity": "",
  "cvss_score": null,
  "cvss_vector": "",
  "remediation": [],
  "matched_cves": [],
  "matched_techniques": [],
  "verdict": null,
  "confidence": null
}

Use null only for verdict, confidence, and cvss_score. Use empty strings or arrays for unknown fields.
Keep evidence factual and concise. Preserve useful endpoint, parameter, version, response-code,
tool-output, and observed-behaviour details. Verdict, confidence, CVEs, and techniques may be
copied from an explicit validation result, but not inferred from general assistant prose. Copy
severity, CVSS score, and CVSS vector only when explicitly stated in admissible content."""

_VALID_VERDICTS = {item.value for item in FindingVerdict}


def _normalize_extraction(data: dict[str, Any]) -> dict[str, Any]:
    """Reject malformed optional classifications without losing factual fields."""
    normalized = dict(data)
    verdict = normalized.get("verdict")
    if isinstance(verdict, str):
        verdict = verdict.strip().lower().replace(" ", "_")
    normalized["verdict"] = verdict if verdict in _VALID_VERDICTS else None

    confidence = normalized.get("confidence")
    if isinstance(confidence, str):
        try:
            confidence = float(confidence.rstrip("%"))
            if confidence > 1:
                confidence /= 100
        except ValueError:
            confidence = None
    normalized["confidence"] = (
        confidence
        if isinstance(confidence, (int, float)) and 0 <= confidence <= 1
        else None
    )
    cvss_score = normalized.get("cvss_score")
    if isinstance(cvss_score, str):
        try:
            cvss_score = float(cvss_score)
        except ValueError:
            cvss_score = None
    normalized["cvss_score"] = (
        cvss_score if isinstance(cvss_score, (int, float)) and 0 <= cvss_score <= 10 else None
    )
    return normalized


@dataclass(frozen=True)
class _Rule:
    key: str
    label: str
    maximum: float
    fields: tuple[str, ...]


_RULES = (
    _Rule("identity", "Finding identity", 1.5, ("title", "description")),
    _Rule("scope", "Affected scope", 1.0, ("affected_scope",)),
    _Rule("evidence", "Technical evidence", 2.0, ("technical_evidence",)),
    _Rule("reproduction", "Reproduction", 1.5, ("reproduction_steps",)),
    _Rule("impact", "Impact and risk rating", 2.0, ("impact", "severity", "cvss_score", "cvss_vector")),
    _Rule("mapping", "References and mapping", 2.0, ("matched_cves", "matched_techniques", "verdict")),
)


def _present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_present(item) for item in value)
    return True


def _dimension(rule: _Rule, draft: ReportDraft) -> ReadinessDimension:
    values = [getattr(draft, field) for field in rule.fields]
    coverage = sum(_present(value) for value in values) / len(values)
    score = round(rule.maximum * coverage, 1)
    return ReadinessDimension(
        key=rule.key,
        label=rule.label,
        score=score,
        max_score=rule.maximum,
        complete=coverage == 1,
    )


def score_report_draft(draft: ReportDraft) -> ReportReadinessResponse:
    """Score extracted fields. The LLM extracts facts but never controls this score."""
    dimensions = [_dimension(rule, draft) for rule in _RULES]
    score = round(sum(item.score for item in dimensions), 1)

    # A report needs an identifiable issue and some technical substance even
    # when the analyst explicitly accepts an incomplete deliverable.
    has_identity = _present(draft.title) and _present(draft.description)
    has_substance = _present(draft.technical_evidence) or _present(draft.reproduction_steps)
    eligible = score >= READINESS_THRESHOLD and has_identity and has_substance

    strengths = [item.label for item in dimensions if item.complete]
    missing = [item.label for item in dimensions if not item.complete]
    if not has_identity:
        summary = "Add a clear finding title and description before building a report."
    elif not has_substance:
        summary = "Add observed technical evidence or reproduction steps before building a report."
    elif score < 7:
        summary = "Minimum report content is present. Missing sections will remain visible for review."
    elif score < 9:
        summary = "The draft is reportable, with a small number of sections still worth strengthening."
    else:
        summary = "The conversation contains a strong, well-supported report draft."

    status = "ready" if score >= 9 else "reportable" if eligible else "not_ready"
    return ReportReadinessResponse(
        score=score,
        eligible=eligible,
        threshold=READINESS_THRESHOLD,
        status=status,
        summary=summary,
        strengths=strengths,
        missing=missing,
        dimensions=dimensions,
        draft=draft,
    )


def _fallback_draft(user_text: str) -> ReportDraft:
    """Preserve analyst content when structured LLM extraction is unavailable."""
    first_line = next((line.strip() for line in user_text.splitlines() if line.strip()), "")
    title = first_line.split(".", 1)[0][:160]
    return ReportDraft(
        title=title,
        description=user_text,
    )


async def assess_conversation(messages: list[ChatMessage]) -> ReportReadinessResponse:
    user_text = "\n".join(message.content for message in messages if message.role == "user").strip()
    if not user_text:
        return score_report_draft(ReportDraft())
    normalized = " ".join(user_text.lower().split()).strip(".!?,")
    if normalized in _SOCIAL_TURNS:
        return score_report_draft(ReportDraft())

    # Ordinary assistant prose can contain suggestions or general knowledge;
    # treating it as source data would let the model fill its own report gaps.
    # Only analyst turns and the explicit structured validation summary count.
    admissible = [
        message
        for message in messages
        if message.role == "user" or "**Validation Complete**" in message.content
    ][-30:]
    transcript = "\n\n".join(
        f"{message.role.upper()}: {message.content[:6000]}" for message in admissible
    )[-50_000:]
    try:
        data = await AsyncLLMClient().generate_validation(
            system=EXTRACTION_SYSTEM,
            user_message=f"CONVERSATION:\n{transcript}\n\nExtract the report draft.",
            max_tokens=1600,
        )
        return score_report_draft(ReportDraft(**_normalize_extraction(data)))
    except Exception as exc:
        log.warning("Structured readiness extraction unavailable: %s", exc)
        result = score_report_draft(_fallback_draft(user_text))
        result.assessment_notice = (
            "Structured AI extraction is temporarily unavailable. This conservative assessment "
            "preserves the analyst's text as source material and leaves unverified fields incomplete."
        )
        result.summary = "A conservative partial assessment was produced from the analyst's text."
        return result


def draft_to_finding(draft: ReportDraft) -> Finding:
    details = [draft.description]
    if draft.affected_scope:
        details.append(f"Affected scope: {draft.affected_scope}")
    if draft.technical_evidence:
        details.append(f"Technical evidence:\n{draft.technical_evidence}")
    if draft.reproduction_steps:
        details.append("Reproduction steps:\n" + "\n".join(
            f"{index}. {step}" for index, step in enumerate(draft.reproduction_steps, 1)
        ))
    if draft.impact:
        details.append(f"Impact:\n{draft.impact}")

    verdict = FindingVerdict(draft.verdict.value) if draft.verdict else None
    finding = Finding(
        title=draft.title or "Conversation finding",
        description="\n\n".join(part for part in details if part),
        verdict=verdict,
        confidence=draft.confidence,
        reasoning=draft.impact or draft.description,
        matched_cves=draft.matched_cves,
        matched_techniques=draft.matched_techniques,
        missing_evidence=[],
        recommended_next_steps=draft.remediation,
    )
    finding.severity = draft.severity
    finding.cvss_score = draft.cvss_score
    finding.cvss_vector = draft.cvss_vector
    return finding
