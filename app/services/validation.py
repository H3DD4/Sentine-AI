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

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.kb.base import SearchOutcome
from app.schemas import ValidationResult
from app.services.llm_client import AsyncLLMClient
from app.services.retrieval import multimodal_search

log = logging.getLogger(__name__)

VALIDATION_SYSTEM = """You are a senior red team analyst and security researcher at Forvis Mazars.
Your job is to validate whether a reported penetration testing finding is a confirmed vulnerability,
a likely issue, insufficient to confirm, or a false positive.

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
  "missing_evidence": ["<what additional evidence would raise confidence>", ...],
  "recommended_next_steps": ["<missing evidence needed to validate or rate the finding>", ...]
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
    )

    # 2. Build the context and coverage blocks.
    kb_context = build_context_block(outcome)

    # 3. Build the evidence block.
    evidence_block = ""
    if evidence_texts:
        evidence_block += "\n\n=== TEXT/LOG EVIDENCE ===\n"
        evidence_block += "\n---\n".join(evidence_texts[:5])

    if image_descriptions:
        evidence_block += "\n\n=== SCREENSHOT/IMAGE EVIDENCE (vision-extracted) ===\n"
        evidence_block += "\n---\n".join(image_descriptions)

    user_message = f"""FINDING TO VALIDATE:
{finding_description}
{evidence_block}

=== DATA COVERAGE ===
{outcome.provenance_line()}

=== KNOWLEDGE BASE CONTEXT ===
{kb_context}

Validate this finding and return your JSON verdict."""

    # 4. Call the LLM.
    client = AsyncLLMClient()
    data = await client.generate_validation(
        system=VALIDATION_SYSTEM,
        user_message=user_message,
        max_tokens=1500,
    )

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
    return await client.describe_image(image_bytes, media_type)
