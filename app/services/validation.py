"""
Validation service — fully async.

Two things changed here beyond the retrieval swap, both aimed at the same
failure mode: the model producing a confident verdict that is not actually
grounded in retrieved data.

1. Retrieval now runs over the evidence as well as the description.  The
   previous version accepted `evidence_texts` and `image_descriptions` and
   then searched on `finding_description` alone, so the version banners and
   error strings that most precisely identify a vulnerability never reached
   the retriever.

2. The prompt states which sources were actually searched, and what was not
   available.  Without that the model cannot distinguish "the KB has no
   matching CVE" from "the CVE source was down", and in both cases it tends
   to fall back on parametric memory and present the result as grounded —
   which is exactly the behaviour this knowledge base exists to replace.
"""

import asyncio
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.kb.base import SearchOutcome
from app.config import settings
from app.schemas import ValidationResult
from app.services.llm_client import AsyncLLMClient
from app.services.retrieval import multimodal_search

log = logging.getLogger(__name__)

DESCRIPTION_CONTEXT_CHARS = 24_000
EVIDENCE_CONTEXT_CHARS = 24_000
IMAGE_CONTEXT_CHARS = 8_000
KB_CONTEXT_CHARS = 16_000

_CVE_ID = re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE)
_ATTACK_ID = re.compile(r"^T\d{4}(?:\.\d{3})?$", re.IGNORECASE)

VALIDATION_SYSTEM = """You are a senior red team analyst and security risk assessor at Forvis Mazars.
Your job is to validate whether a reported penetration testing finding is a confirmed vulnerability,
a likely issue, insufficient to confirm, or a false positive, then assess its target-specific impact.

You are given context retrieved from a knowledge base of CVEs, MITRE ATT&CK techniques, prior
engagement findings, and internal documentation. Each context entry is labelled with the source it
came from. Ground your analysis in that context.

You MUST respond ONLY with a valid JSON object matching this exact schema:
{
  "verdict": "confirmed" | "likely" | "insufficient" | "false_positive",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<clear explanation of why this verdict was chosen>",
  "matched_cves": ["CVE-YYYY-NNNNN", ...],
  "matched_techniques": ["T1234", "T1234.001", ...],
  "mappings": [{
    "mapping_type": "cve" | "cwe" | "owasp" | "attack",
    "identifier": "<exact identifier>",
    "name": "<canonical name if present in context>",
    "applicability": "direct" | "supporting" | "conditional" | "rejected" | "unsupported",
    "rationale": "<why it does or does not apply to this finding>",
    "evidence_basis": "<current-target evidence supporting applicability>",
    "source": "<retrieved source key, or empty>",
    "source_doc_id": "<retrieved document ID, or empty>"
  }],
  "missing_evidence": ["<what additional evidence would raise confidence>", ...],
  "recommended_next_steps": ["<missing evidence needed to validate or rate the finding>", ...],
  "impact_assessment": {
    "demonstrated_capability": "<the strongest attacker capability directly established by evidence>",
    "technical_impact": "<bounded confidentiality, integrity, availability, privilege, or accountability effect>",
    "affected_assets": ["<known target-specific assets only>"],
    "affected_data": ["<known data classes and demonstrated scope only>"],
    "affected_business_processes": ["<known processes only>"],
    "business_impact": "<target-specific consequence supported by supplied context, or state that it is pending>",
    "business_priority": "critical" | "high" | "moderate" | "low" | "pending_context",
    "priority_rationale": "<why this priority follows from evidence and target context>",
    "cvss": {
      "status": "exact" | "range" | "pending_evidence" | "not_applicable",
      "version": "4.0" | "3.1" | "",
      "vector": "<complete vector or empty>",
      "score": <float 0.0-10.0 or null>,
      "severity": "critical" | "high" | "medium" | "low" | "none" | "",
      "rationale": "<why the result is exact, ranged, pending, or not applicable>",
      "lower_bound": {"label": "<evidence-established scenario>", "vector": "<vector>", "score": <0-10>, "severity": "<label>", "assumptions": [], "rationale": "<metric rationale>"} | null,
      "upper_bound": {"label": "<credible conditional scenario>", "vector": "<vector>", "score": <0-10>, "severity": "<label>", "assumptions": ["<required unverified fact>"], "rationale": "<metric rationale>"} | null,
      "unresolved_metrics": ["<metric and the exact missing fact that changes it>"]
    },
    "claims": [{
      "level": "observed" | "logically_demonstrated" | "conditional",
      "statement": "<one material impact claim>",
      "evidence_basis": "<specific submitted evidence, verified permission, architecture fact, or owner statement>",
      "conditions": ["<unverified prerequisites; empty for observed claims>"]
    }],
    "excluded_claims": ["<material consequence explicitly not established>"],
    "assumptions": ["<assumption that still limits the assessment>"],
    "clarification_questions": [{
      "question": "<one concise question answerable by the analyst>",
      "why_it_matters": "<the exact impact or priority decision this answer can change>",
      "answer_options": ["<short likely options when useful>"]
    }],
    "context_complete": true | false
  }
}

Rules:
- "confirmed": strong evidence matches a known CVE or technique, reproduction possible
- "likely": strong indicators but evidence is incomplete
- "insufficient": cannot determine without more evidence
- "false_positive": evidence contradicts the finding or indicates a benign condition
- Do not fabricate CVE IDs or technique IDs. Only reference identifiers that appear in the
  provided context. If none appear, return empty lists rather than recalling them from memory.
- The DATA COVERAGE block states which knowledge sources were searched. If a source was
  unavailable, treat its subject area as unverified: lower your confidence and name the gap in
  "missing_evidence" rather than answering from your own knowledge as if it were retrieved.
- If no context was retrieved at all, the only defensible verdicts are "insufficient" or
  "false_positive", and "reasoning" must say the finding could not be checked against the KB.
- Be specific in reasoning — name the exact evidence that led to each conclusion.
- Treat finding validity, technical severity, validation confidence, and business priority as separate
  decisions. CVSS is technical severity and must not be used as a substitute for business impact.
- Prefer CVSS 4.0; use 3.1 only when that is the engagement convention. Use status=exact only when every
  material metric is evidence-established. If unknown facts can change metrics, use status=range with:
  (1) a lower scenario limited to the demonstrated outcome, (2) an upper credible scenario whose every
  assumption is explicit, and (3) unresolved_metrics naming what separates them. A range is not an exact
  score. Use pending_evidence when even a defensible lower scenario cannot be calculated. Never copy a
  retrieved CVE score onto this finding or use an unverified worst case as the primary score.
- mappings is the authoritative classification analysis. "direct" means the identifier describes the
  current weakness or demonstrated behavior; "supporting" means relevant context but not the root cause;
  "conditional" requires an unverified action or condition; "rejected" is an analyst/scanner suggestion
  contradicted by evidence; "unsupported" lacks authoritative retrieved backing. Every source-backed
  mapping must name the exact retrieved source and source_doc_id. Similar CVEs are supporting at most,
  never matched CVEs. matched_cves must contain only direct CVEs for the affected product/version;
  matched_techniques must contain only direct ATT&CK behavior demonstrated in the current test.
- Build impact as: verified evidence -> demonstrated capability -> affected asset/data/process ->
  organizational consequence. Stop the chain at the first unverified prerequisite.
- "observed" means directly performed or returned during the authorized test.
- "logically_demonstrated" requires a verified permission, architecture, code, data-flow, or owner fact
  that completes the path even though the final business action was intentionally not executed.
- "conditional" must name every material unverified condition. Never phrase it as established impact.
- Put dramatic but unsupported outcomes such as full account takeover, all-customer data exposure,
  regulatory fines, ransomware, or business shutdown in excluded_claims when they are relevant enough
  that a reader might otherwise infer them.
- Set business_priority to pending_context when target-specific facts are insufficient. Do not guess
  production status, asset criticality, data classification or volume, role permissions, downstream
  trust, control effectiveness, financial loss, regulatory scope, safety consequence, or customer reach.
- Ask zero questions when context is sufficient. Otherwise ask one compact batch of at most three
  clarification questions. Select only questions whose answers could materially change the business
  priority or the strongest defensible impact claim. Combine related facts into one question, provide
  short answer_options when they reduce analyst effort, and explain why each answer matters. Do not ask
  for information already present and do not ask generic discovery questions.
- context_complete is true only when the target-specific business impact and priority are supportable
  without material assumptions. A technically confirmed finding may still have context_complete=false.
- recommended_next_steps is only for evidence collection needed to validate or accurately rate
  the finding. Do not return fixes, patches, upgrades, WAF rules, hardening, mitigation, or
  remediation advice.
- Do not suggest additional post-exploitation commands when existing evidence already confirms
  the claimed impact.
- No prose outside the JSON object. No markdown fences. Raw JSON only."""


def build_context_block(outcome: SearchOutcome) -> str:
    """
    Render retrieved hits with their source labels.

    The source label on every entry is not decoration: it is what lets the
    model — and the analyst reading the reasoning afterwards — tell a public
    CVE record apart from a colleague's prior finding, which carry very
    different weight when deciding whether something is exploitable here.
    """
    if not outcome.hits:
        return "No relevant knowledge base entries were retrieved for this finding."

    blocks = []
    for i, hit in enumerate(outcome.hits, 1):
        payload = hit.payload or {}
        lines = [f"[{i}] SOURCE: {hit.source_label} | ID: {hit.doc_id}"]
        if hit.title and hit.title != hit.doc_id:
            lines.append(f"Title: {hit.title}")

        cvss = payload.get("cvss_v3")
        severity = payload.get("severity")
        if cvss or severity:
            lines.append(f"CVSS: {cvss or 'N/A'} ({severity or 'unrated'})")

        for label, key in (
            ("CWE", "cwe"),
            ("Tactics", "tactics"),
            ("Platforms", "platforms"),
            ("MITRE", "mitre_techniques"),
            ("Affected", "affected_products"),
        ):
            value = payload.get(key)
            if not value:
                continue
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value[:8])
            lines.append(f"{label}: {value}")

        lines.append(f"Content: {hit.text}")

        for label, key in (
            ("Detection", "detection"),
            ("Validation steps", "validation_steps"),
            ("Mitigation", "mitigation"),
        ):
            value = payload.get(key)
            if not value:
                continue
            if isinstance(value, list):
                value = "; ".join(str(v) for v in value[:5])
            lines.append(f"{label}: {str(value)[:600]}")

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def impact_narrative(result: ValidationResult) -> str:
    """Render a report-safe summary while retaining the structured assessment separately."""
    impact = result.impact_assessment
    sections = []
    if impact.demonstrated_capability:
        sections.append(f"Demonstrated capability: {impact.demonstrated_capability}")
    if impact.technical_impact:
        sections.append(f"Technical impact: {impact.technical_impact}")
    if impact.business_impact:
        sections.append(f"Business impact: {impact.business_impact}")
    if impact.priority_rationale:
        priority = impact.business_priority.value.replace("_", " ").title()
        sections.append(f"Business priority: {priority}. {impact.priority_rationale}")
    conditional = [claim.statement for claim in impact.claims if claim.level.value == "conditional"]
    if conditional:
        sections.append("Conditional impact: " + " ".join(conditional))
    if impact.excluded_claims:
        sections.append("Not established: " + "; ".join(impact.excluded_claims))
    return "\n\n".join(sections)


def _normalize_assessment(data: dict, outcome: SearchOutcome) -> None:
    """Apply provenance and compatibility guards after generation, before persistence."""
    assessment = data.get("impact_assessment")
    if isinstance(assessment, dict):
        questions = assessment.get("clarification_questions")
        if isinstance(questions, list):
            assessment["clarification_questions"] = questions[:3]

    retrieved = {
        (hit.source_key.lower(), str(hit.doc_id).upper())
        for hit in outcome.hits
    }
    normalized_mappings = []
    for raw in data.get("mappings") or []:
        if not isinstance(raw, dict):
            continue
        mapping = dict(raw)
        identifier = str(mapping.get("identifier") or "").strip().upper()
        source = str(mapping.get("source") or "").strip().lower()
        source_doc_id = str(mapping.get("source_doc_id") or "").strip().upper()
        applicability = str(mapping.get("applicability") or "unsupported")
        mapping["identifier"] = identifier
        if applicability in {"direct", "supporting", "conditional"}:
            if not source or not source_doc_id or (source, source_doc_id) not in retrieved:
                mapping["applicability"] = "unsupported"
                mapping["source"] = ""
                mapping["source_doc_id"] = ""
                mapping["rationale"] = (
                    str(mapping.get("rationale") or "")
                    + " No exact retrieved authoritative entry backed this mapping."
                ).strip()
        normalized_mappings.append(mapping)
    data["mappings"] = normalized_mappings

    # Legacy flat arrays remain intentionally strict: only direct, source-backed
    # identifiers reach reports and integrations that cannot represent nuance.
    data["matched_cves"] = [
        item["identifier"]
        for item in normalized_mappings
        if item.get("mapping_type") == "cve"
        and item.get("applicability") == "direct"
        and _CVE_ID.fullmatch(item.get("identifier", ""))
    ]
    data["matched_techniques"] = [
        item["identifier"]
        for item in normalized_mappings
        if item.get("mapping_type") == "attack"
        and item.get("applicability") == "direct"
        and _ATTACK_ID.fullmatch(item.get("identifier", ""))
    ]


def _representative_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head = limit * 2 // 3
    tail = limit - head
    return text[:head] + "\n\n[... middle omitted from model context ...]\n\n" + text[-tail:], True


def _bounded_items(items: list[str], budget: int) -> tuple[list[str], bool]:
    if not items:
        return [], False
    per_item = max(1, budget // len(items))
    selected = []
    truncated = False
    for item in items:
        excerpt, was_truncated = _representative_text(item, per_item)
        selected.append(excerpt)
        truncated = truncated or was_truncated
    return selected, truncated


async def validate_finding(
    finding_description: str,
    evidence_texts: list[str],
    image_descriptions: list[str],
    session: AsyncSession,
) -> tuple[ValidationResult, SearchOutcome]:
    """
    Validate a finding against the knowledge base.

    Returns the verdict *and* the search outcome. The outcome is returned
    rather than discarded so the caller can show the analyst which sources
    backed the verdict — a verdict without its provenance is not auditable,
    and this tool's output ends up in client deliverables.
    """
    # 1. Retrieve across every modality of the finding.
    outcome = await multimodal_search(
        finding_description,
        session,
        evidence_texts=evidence_texts,
        image_descriptions=image_descriptions,
        rerank=True,
    )

    # 2. Build the context and coverage blocks.
    kb_context, kb_truncated = _representative_text(
        build_context_block(outcome), KB_CONTEXT_CHARS
    )

    finding_context, finding_truncated = _representative_text(
        finding_description, DESCRIPTION_CONTEXT_CHARS
    )
    selected_evidence, evidence_truncated = _bounded_items(
        evidence_texts, EVIDENCE_CONTEXT_CHARS
    )
    selected_images, images_truncated = _bounded_items(
        image_descriptions, IMAGE_CONTEXT_CHARS
    )
    if finding_truncated or evidence_truncated or images_truncated or kb_truncated:
        outcome.notes.append(
            "validation used representative bounded excerpts; complete submitted text and artifacts "
            "remain stored where persistence was requested"
        )

    # 3. Build the evidence block.
    evidence_block = ""
    if selected_evidence:
        evidence_block += "\n\n=== TEXT/LOG EVIDENCE ===\n"
        evidence_block += "\n---\n".join(selected_evidence)

    if selected_images:
        evidence_block += "\n\n=== SCREENSHOT/IMAGE EVIDENCE (vision-extracted) ===\n"
        evidence_block += "\n---\n".join(selected_images)

    user_message = f"""FINDING TO VALIDATE:
{finding_context}
{evidence_block}

=== DATA COVERAGE ===
{outcome.provenance_line()}

=== KNOWLEDGE BASE CONTEXT ===
{kb_context}

Validate this finding and return your JSON verdict."""

    # 4. Call the LLM.
    client = AsyncLLMClient()
    async with asyncio.timeout(settings.VALIDATION_STAGE_TIMEOUT_SECONDS):
        data = await client.generate_validation(
            system=VALIDATION_SYSTEM,
            user_message=user_message,
        max_tokens=2800,
        )

    _normalize_assessment(data, outcome)
    result = ValidationResult(**data)

    # 5. A verdict can never be more certain than its evidence. When a source
    #    was unreachable the model was working from partial coverage, so an
    #    unqualified high confidence would misrepresent it to the analyst.
    if outcome.degraded and result.confidence > 0.7:
        log.info(
            "Capping confidence %.2f → 0.7: retrieval was degraded (%s)",
            result.confidence,
            "; ".join(r.display_name for r in outcome.sources_failed),
        )
        result.confidence = 0.7

    return result, outcome


async def describe_image(image_bytes: bytes, media_type: str) -> str:
    """Use vision model to extract text/context from an uploaded screenshot."""
    client = AsyncLLMClient()
    async with asyncio.timeout(settings.VISION_STAGE_TIMEOUT_SECONDS):
        return await client.describe_image(image_bytes, media_type)
