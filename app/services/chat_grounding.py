"""Structured, deterministic grounding for conversational security analysis.

The model drafts narrative and proposes claims. Python owns every value that can
be proven mechanically: source identity, canonical titles, mapping admission,
evidence references, CVSS arithmetic, provenance, and final rendering.
"""

from __future__ import annotations

import json
import html
import re
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.kb.base import RetrievalHit, SearchOutcome
from app.schemas import CVSSAssessment, ImpactClaim, SecurityMapping
from app.services.llm_client import AsyncLLMClient
from app.services.validation import _calculated_cvss, _normalize_assessment, build_context_block


StageCallback = Callable[[str, str, str, str], Awaitable[None]]

_CVSS_VECTOR_TOKEN = r"CVSS:3\.[01](?:/[A-Z]+:[A-Z])+|CVSS:4\.0(?:/[A-Z]+:[A-Z]+)+"
_AUTHORITATIVE_TOKEN = re.compile(
    r"\bCVE-\d{4}-\d{4,}\b|\bT\d{4}(?:\.\d{3})?\b|"
    r"\bCWE-\d+\b|\bA(?:0[1-9]|10):20\d{2}\b|"
    r"\b(?:(?:TII|TIS|ASIA)_)?(?:BP|V)_\d{3}\b|" + _CVSS_VECTOR_TOKEN,
    re.IGNORECASE,
)
_KB_REFERENCE = re.compile(r"\[KB(\d+)\]", re.IGNORECASE)
_USER_CVSS_VECTOR = re.compile(_CVSS_VECTOR_TOKEN, re.IGNORECASE)
_LEAKED_PLACEHOLDER = re.compile(
    r"\[(?:authoritative value (?:rendered below|omitted)|"
    r"unsupported local citation removed)\]",
    re.IGNORECASE,
)
_MODEL_CVSS_RANGE = re.compile(
    r"(?i)\b(?:lower\s+bound|upper\s+bound|borne\s+(?:inf[eé]rieure|sup[eé]rieure)|"
    r"statut\s*[:=-]?\s*`?range|status\s*[:=-]?\s*`?range|"
    r"technical\s+severity\s+range)\b"
)
_INLINE_CVSS_SCORE = re.compile(
    r"(?i)(?:"
    r"(?:cvss(?:\s*v?3\.1|\s*v?4\.0)?|score(?:\s+de\s+base|\s+technique)?|"
    r"base\s+score|technical\s+score|note\s+cvss|s[eé]v[eé]rit[eé])"
    r"[^\n.!?]{0,48}\b(?:10(?:\.0)?|[0-9](?:\.\d)?)\b"
    r"|"
    # Score-first shorthand must be decimal-shaped. Requiring a decimal avoids
    # treating ordinary counts such as "3 comptes à privilège faible" as CVSS.
    r"\b(?:10\.0|[0-9]\.\d)\b[^\n.!?]{0,32}"
    r"(?:cvss|critique|critical|high|medium|low|informational)"
    r")"
)
_INLINE_SEVERITY_LABEL = re.compile(
    r"(?i)(?:s[eé]v[eé]rit[eé]|criticit[eé](?:\s+technique)?|severity)\s*[:=-]\s*"
    r"(?:critical|critique|high|[ée]lev[ée]e?|medium|moyenne?|low|faible|none|aucune)"
)
_CVSS_METRIC_TOKEN = re.compile(
    r"(?i)(?<![A-Z0-9])(?:"
    r"(?P<AV>AV):[NALP]|(?P<AC>AC):[LH]|(?P<AT>AT):[NP]|"
    r"(?P<PR>PR):[NLH]|(?P<UI>UI):[NPR]|(?P<S>S):[UC]|"
    r"(?P<C>C):[NLH]|(?P<I>I):[NLH]|(?P<A>A):[NLH]|"
    r"(?P<E>E):[XUAPF]|(?P<RL>RL):[XOTWU]|(?P<RC>RC):[XURC]|"
    r"(?P<CR>CR):[XLMH]|(?P<IR>IR):[XLMH]|(?P<AR>AR):[XLMH]|"
    r"(?P<MAV>MAV):[XNALP]|(?P<MAC>MAC):[XLH]|(?P<MPR>MPR):[XNLH]|"
    r"(?P<MUI>MUI):[XNPR]|(?P<MS>MS):[XUC]|(?P<MC>MC):[XNLH]|"
    r"(?P<MI>MI):[XNLH]|(?P<MA>MA):[XNLH]|"
    r"(?P<VC>VC):[NLH]|(?P<VI>VI):[NLH]|(?P<VA>VA):[NLH]|"
    r"(?P<SC>SC):[NLH]|(?P<SI>SI):[NLH]|(?P<SA>SA):[NLH]|"
    r"(?P<AU>AU):[NY]|(?P<R>R):[AUXI]|(?P<V>V):[DC]|"
    r"(?P<RE>RE):[LMHX]|(?P<U>U):(?:Clear|Green|Amber|Red|X)"
    r")(?![A-Z0-9À-ÖØ-öø-ÿ])"
)
_CVSS_LABEL_VALUE = re.compile(
    r"(?i)(?P<label>"
    r"attack\s+vector|vecteur\s+d['’]attaque|attack\s+complexity|complexit[eé]|"
    r"privileges?\s+required|privil[eè]ges?\s+requis|user\s+interaction|"
    r"interaction\s+utilisateur|scope|port[eé]e|confidentiality|confidentialit[eé]|"
    r"integrity|int[eé]grit[eé]|availability|disponibilit[eé]"
    r")\s*(?:\([^)]*\))?\s*[:=-]\s*(?P<value>[^\n;|]{1,48})"
)
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_CANONICAL_CITATION = re.compile(
    r"\[(?:nvd|mitre|owasp|owasp_docs|ghostwriter|internal|finding_templates):[^\]\s]+\]",
    re.IGNORECASE,
)
_INTERNAL_CITATION_TOKEN = re.compile(r"__GROUNDING_CITATION_[A-Za-z0-9_-]+__")


class ProposedCVSSScenario(BaseModel):
    # Providers often include score/severity despite the schema instruction.
    # Ignore those untrusted extras; Python recomputes them after parsing.
    model_config = ConfigDict(extra="ignore")
    label: str = ""
    vector: str = ""
    assumptions: list[str] = Field(default_factory=list)
    rationale: str = ""


class ProposedCVSSAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["exact", "range", "pending_evidence", "not_applicable"] = "pending_evidence"
    version: Literal["4.0", "3.1", ""] | None = ""
    vector: str | None = ""
    rationale: str = ""
    lower_bound: ProposedCVSSScenario | None = None
    upper_bound: ProposedCVSSScenario | None = None
    unresolved_metrics: list[str] = Field(default_factory=list)


class ProposedSecurityMapping(BaseModel):
    """Permissive model-output shape; validation owns admissibility later."""

    model_config = ConfigDict(extra="ignore")
    mapping_type: str = ""
    identifier: str = ""
    name: str = ""
    applicability: str = "unsupported"
    rationale: str = ""
    evidence_basis: str = ""
    source: str = ""
    source_doc_id: str = ""


class GroundedChatDraft(BaseModel):
    """Model-authored proposal. None of its authoritative fields are trusted yet."""

    answer_markdown: str = ""
    claims: list[ImpactClaim] = Field(default_factory=list)
    mappings: list[ProposedSecurityMapping] = Field(default_factory=list)
    cvss: ProposedCVSSAssessment = Field(default_factory=ProposedCVSSAssessment)
    limitations: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class ValidatedChatDraft(BaseModel):
    """Python-normalized draft. Authoritative values may exist only here."""

    answer_markdown: str = ""
    claims: list[ImpactClaim] = Field(default_factory=list)
    mappings: list[SecurityMapping] = Field(default_factory=list)
    cvss: CVSSAssessment = Field(default_factory=CVSSAssessment)
    limitations: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


@dataclass
class GroundingResult:
    draft: ValidatedChatDraft
    issues: list[str] = field(default_factory=list)
    corrected: bool = False
    valid_kb_references: list[int] = field(default_factory=list)
    requested_identifiers: list[str] = field(default_factory=list)
    itemized_cvss: bool = False
    generation_status: Literal["model", "corrected_model", "deterministic_fallback"] = "model"


CHAT_DRAFT_SYSTEM = """You are drafting a grounded cybersecurity answer for a red-team analyst.
Return ONLY one JSON object with this schema:
{
  "answer_markdown": "Useful narrative in the user's language",
  "claims": [{
    "level": "observed" | "logically_demonstrated" | "conditional",
    "statement": "one material current-target claim",
    "evidence_basis": "specific basis",
    "evidence_ids": ["FINDING1", "KB1", "TEXT1", "IMAGE1"],
    "conditions": ["unverified condition"]
  }],
  "mappings": [{
    "mapping_type": "cve" | "cwe" | "owasp" | "attack" | "template",
    "identifier": "exact retrieved identifier",
    "name": "",
    "applicability": "direct" | "supporting" | "conditional" | "rejected" | "unsupported",
    "rationale": "why it applies",
    "evidence_basis": "current-target basis",
    "source": "exact source key",
    "source_doc_id": "exact retrieved document ID"
  }],
  "cvss": {
    "status": "exact" | "range" | "pending_evidence" | "not_applicable",
    "version": "4.0" | "3.1" | "",
    "vector": "",
    "rationale": "",
    "lower_bound": {"label": "", "vector": "", "assumptions": [], "rationale": ""} | null,
    "upper_bound": {"label": "", "vector": "", "assumptions": [], "rationale": ""} | null,
    "unresolved_metrics": []
  },
  "limitations": [],
  "assumptions": []
}

Rules:
- Answer the user's actual request and preserve their language.
- Narrative may cite retrieved records only as [KB1], [KB2], etc. Never write a CVE,
  ATT&CK ID, internal template ID, local document ID, canonical source title, CVSS vector,
  score, or severity in answer_markdown. Python renders those immutable values.
- Put every proposed authoritative mapping in mappings, using an exact retrieved source and ID.
- Current-target facts come from FINDING1/TEXTn/IMAGEn. KBn is context, never target proof.
- Observed and logically_demonstrated claims require current-target evidence. Conditional claims
  must list every material missing condition.
- Never infer production status, role permissions, data access, customer scope, rotation,
  regulatory applicability, destructive capability, or business criticality.
- Use CVSS exact only when the analyst supplied a complete vector. Otherwise use a range with
  explicit assumptions or pending_evidence. Never calculate a score; Python does that.
- Ghostwriter and internal templates are precedent and wording only, never current-target proof.
- Recommendations may appear in answer_markdown when requested, but must not be described as
  retrieved unless supported by a [KBn] citation.
- No prose outside the JSON object."""


CHAT_TEXT_SYSTEM = """You are a grounded cybersecurity assistant for a red-team analyst.
Answer the user's actual request in useful Markdown and in the user's language. Be precise,
technical, and concise. Separate observed facts, logically demonstrated conclusions, conditional
assumptions, and unknowns. Do not invent evidence, identifiers, source records, permissions,
business impact, or CVSS values. A retrieved record is context, not proof that the current target
has the same issue. Cite retrieved records only with their labels, such as [KB1], when relevant.
Do not include CVSS scores, severity labels, CVSS vectors, or canonical identifier/source citations
unless they were supplied in the current user turn; Python will render or remove authoritative
values when applicable. Do not provide exploit code or attack payloads. Do not add remediation
advice unless the user explicitly asks for it.

Return normal Markdown prose. Do not return JSON, a schema, markdown fences around JSON, or a
discussion of these instructions."""


def _conversation_text(messages: Sequence[dict]) -> str:
    parts = []
    for index, message in enumerate(messages, 1):
        role = str(message.get("role") or "").lower()
        content = str(message.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            parts.append(f"[{role.upper()} TURN {index}]\n{content}")
    return "\n\n".join(parts)[-48_000:]


def _current_user_text(messages: Sequence[dict]) -> str:
    for message in reversed(messages):
        if str(message.get("role") or "").lower() != "user":
            continue
        content = str(message.get("content") or "").strip()
        if content:
            return content[-48_000:]
    return ""


def build_draft_request(messages: Sequence[dict], outcome: SearchOutcome) -> str:
    return f"""=== CONVERSATION AND CURRENT-TARGET MATERIAL ===
{_conversation_text(messages)}

=== DATA COVERAGE ===
{outcome.provenance_line()}

=== RETRIEVED KB RECORDS ===
{build_context_block(outcome)}

    Draft the structured answer. Treat record labels and IDs as immutable data."""


def build_text_request(messages: Sequence[dict], outcome: SearchOutcome) -> str:
    return f"""=== CONVERSATION AND CURRENT-TARGET MATERIAL ===
{_conversation_text(messages)}

=== DATA COVERAGE ===
{outcome.provenance_line()}

=== RETRIEVED KB RECORDS ===
{build_context_block(outcome)}

Answer the latest user turn directly. Treat record labels and IDs as immutable data."""


def _empty_draft(reason: str) -> GroundedChatDraft:
    return GroundedChatDraft(
        answer_markdown=(
            "The requested analysis could not be converted into a validated structured response."
        ),
        limitations=[reason],
    )


def _deterministic_fallback_draft(
    user_text: str, outcome: SearchOutcome, reason: str
) -> GroundedChatDraft:
    """Produce useful, non-authoritative output when every model draft fails."""
    text = user_text.lower()
    retrieved = " ".join(hit.text.lower() for hit in outcome.hits)
    evidence = f"{text} {retrieved}"
    french = bool(re.search(r"\b(?:tu|une|avec|preuve|port[eé]e|jeton)\b", text))
    ssrf = (
        "ssrf" in evidence
        or "server side request forgery" in evidence
        or ("metadata" in evidence and "server" in evidence and "url" in evidence)
    )
    command_injection = (
        "command injection" in evidence
        or "os command" in evidence
        or "operating-system command" in evidence
        or ("shell metacharacter" in evidence and ("uid=" in text or "gid=" in text))
    )
    command_output = " ".join(dict.fromkeys(
        match.group(0) for match in re.finditer(
            r"\b(?:uid|gid)=\d+(?:\([^\r\n)]{1,80}\))?", user_text, re.IGNORECASE
        )
    ))
    timeout = "timeout" in text or "time out" in text
    mechanism = (
        f"Le matériel fourni contient la sortie {command_output}, cohérente avec l'exécution d'une commande système via le paramètre testé."
        if french and command_injection and command_output else
        f"The supplied material contains {command_output}, consistent with OS command injection through the tested parameter."
        if command_injection and command_output else
        "Le contexte récupéré contient une référence à un mécanisme d'injection de commande système."
        if french and command_injection else
        "The retrieved context contains a reference to an OS command injection mechanism."
        if command_injection else
        "Le contexte récupéré contient une référence au mécanisme SSRF."
        if french and ssrf else
        "Retrieved context contains a reference to the SSRF mechanism."
        if ssrf else
        "The retrieved context does not establish an exact vulnerability mechanism."
    )
    contradiction = (
        "Une contradiction ou un résultat de timeout doit rester non résolu et être vérifié dans le même environnement."
        if french and timeout else
        "A contradictory or timeout result remains unresolved and must be verified in the same environment."
        if timeout else "No cross-run contradiction was deterministically established."
    )
    missing = (
        "Les métriques CVSS, l'impact et les correspondances autoritatives restent à confirmer par des preuves."
        if french else
        "CVSS metrics, impact, and authoritative mappings remain to be confirmed with evidence."
    )
    heading = "Analyse conservatrice" if french else "Conservative analysis"
    answer = (
        f"## {heading}\n\n"
        + ("Le modèle n'a pas fourni de brouillon structuré valide. Le système conserve uniquement les faits contrôlables.\n\n" if french
           else "The model did not provide a valid structured draft. The system retains only mechanically supportable facts.\n\n")
        + mechanism + "\n\n"
        + contradiction + "\n\n"
        + missing
    )
    return GroundedChatDraft(
        answer_markdown=answer,
        cvss=ProposedCVSSAssessment(
            status="pending_evidence",
            rationale=missing,
            unresolved_metrics=["Analyst confirmation required before scoring."],
        ),
        limitations=[reason],
    )


def parse_draft(data: dict) -> tuple[GroundedChatDraft, list[str]]:
    """Parse model JSON without guessing missing nested values."""
    try:
        return GroundedChatDraft.model_validate(data), []
    except ValidationError as exc:
        return _empty_draft("The generated structured draft failed schema validation."), [
            f"Structured draft schema error: {error['loc']}: {error['msg']}"
            for error in exc.errors()[:12]
        ]


def _draft_is_substantively_empty(draft: ValidatedChatDraft) -> bool:
    return not any((
        draft.answer_markdown.strip(),
        draft.claims,
        draft.mappings,
        draft.limitations,
        draft.assumptions,
    ))


def _normalize_draft(
    draft: GroundedChatDraft,
    outcome: SearchOutcome,
    *,
    user_text: str,
    text_evidence_count: int = 0,
    image_evidence_count: int = 0,
) -> ValidatedChatDraft:
    """Reuse the report validator for mappings, claims, and CVSS arithmetic."""
    assessment = {
        "cvss": draft.cvss.model_dump(mode="json"),
        "claims": [claim.model_dump(mode="json") for claim in draft.claims],
        "clarification_questions": [],
    }
    wrapper = {
        "mappings": [mapping.model_dump(mode="json") for mapping in draft.mappings],
        "matched_cves": [],
        "matched_techniques": [],
        "missing_evidence": [],
        "recommended_next_steps": [],
        "impact_assessment": assessment,
    }
    _normalize_assessment(
        wrapper,
        outcome,
        text_evidence_count=text_evidence_count,
        image_evidence_count=image_evidence_count,
    )

    cvss = assessment.get("cvss") or {}
    supplied_vectors = {
        _vector_identity(value) for value in _USER_CVSS_VECTOR.findall(user_text)
    }
    status = cvss.get("status")
    proposed_identities = []
    if status == "exact":
        proposed_identities = [_vector_identity(cvss.get("vector", ""))]
    elif status == "range":
        proposed_identities = [
            _vector_identity((cvss.get("lower_bound") or {}).get("vector", "")),
            _vector_identity((cvss.get("upper_bound") or {}).get("vector", "")),
        ]
    vectors_admissible = (
        bool(proposed_identities)
        and all(identity is not None and identity in supplied_vectors for identity in proposed_identities)
    )
    if status in {"exact", "range"} and not vectors_admissible:
        cvss = {
            "status": "pending_evidence",
            "version": "",
            "vector": "",
            "score": None,
            "severity": "",
            "rationale": (
                "The model proposed CVSS metrics that were not supplied in the analyst's current "
                "turn; metric applicability requires analyst review."
            ),
            "lower_bound": None,
            "upper_bound": None,
            "unresolved_metrics": [
                "Confirm each CVSS metric with the analyst before assigning an exact score."
            ],
        }

    return ValidatedChatDraft(
        answer_markdown=draft.answer_markdown,
        mappings=[SecurityMapping.model_validate(item) for item in wrapper["mappings"]],
        claims=[ImpactClaim.model_validate(item) for item in assessment.get("claims") or []],
        cvss=CVSSAssessment.model_validate(cvss),
        limitations=draft.limitations,
        assumptions=draft.assumptions,
    )


def validate_draft(
    draft: GroundedChatDraft,
    outcome: SearchOutcome,
    *,
    user_text: str,
    text_evidence_count: int = 0,
    image_evidence_count: int = 0,
) -> GroundingResult:
    """Validate only facts Python can prove, conservatively preserving semantics."""
    itemized_cvss = _draft_has_itemized_cvss(draft)
    normalized = _normalize_draft(
        draft,
        outcome,
        user_text=user_text,
        text_evidence_count=text_evidence_count,
        image_evidence_count=image_evidence_count,
    )
    issues: list[str] = []
    valid_refs: list[int] = []

    for match in _KB_REFERENCE.finditer(normalized.answer_markdown):
        index = int(match.group(1))
        if 1 <= index <= len(outcome.hits):
            valid_refs.append(index)
        else:
            issues.append(f"Narrative cited nonexistent KB{index}.")

    forbidden = sorted({match.group(0).upper() for match in _AUTHORITATIVE_TOKEN.finditer(
        normalized.answer_markdown
    )})
    if forbidden:
        issues.append(
            "Narrative contains model-rendered authoritative values that Python must own: "
            + ", ".join(forbidden)
        )
    if _INLINE_CVSS_SCORE.search(normalized.answer_markdown):
        issues.append(
            "Narrative contains a model-authored CVSS score; Python must render scores from "
            "the validated structured CVSS assessment."
        )
    if _INLINE_SEVERITY_LABEL.search(normalized.answer_markdown):
        issues.append(
            "Narrative contains a model-authored severity label; Python must render severity "
            "from the validated structured CVSS assessment."
        )
    if itemized_cvss:
        issues.append(
            "Narrative contains an itemized CVSS vector; Python must render metric assignments "
            "from the validated structured CVSS assessment."
        )
    if _LEAKED_PLACEHOLDER.search(normalized.answer_markdown):
        issues.append("Narrative contains an internal renderer placeholder.")

    unsupported = [
        mapping.identifier for mapping in normalized.mappings
        if str(mapping.applicability) == "unsupported" and mapping.identifier
    ]
    if unsupported:
        issues.append(
            "Proposed mappings lacked an exact authoritative retrieved record: "
            + ", ".join(dict.fromkeys(unsupported))
        )

    if not outcome.hits and (normalized.mappings or valid_refs):
        issues.append("The draft cited local knowledge although no KB record was retrieved.")

    return GroundingResult(
        draft=normalized,
        issues=list(dict.fromkeys(issues)),
        valid_kb_references=list(dict.fromkeys(valid_refs)),
        requested_identifiers=list(dict.fromkeys(
            match.group(0).upper() for match in _AUTHORITATIVE_TOKEN.finditer(user_text)
            if not match.group(0).upper().startswith("CVSS:")
        )),
        itemized_cvss=itemized_cvss,
    )


def revision_request(result: GroundingResult, outcome: SearchOutcome) -> str:
    ledger = "\n".join(
        f"KB{index}: source={hit.source_key}; doc_id={hit.doc_id}; title={hit.title}"
        for index, hit in enumerate(outcome.hits, 1)
    ) or "No record was retrieved."
    return (
        "Return a corrected JSON draft using the same schema. Correct every deterministic issue. "
        "Keep useful narrative and the user's language. Do not discuss the correction process.\n\n"
        + "\n".join(f"- {issue}" for issue in result.issues)
        + "\n\nIMMUTABLE LEDGER:\n"
        + ledger
    )


def _canonical_mapping(mapping: SecurityMapping, outcome: SearchOutcome) -> tuple[str, str] | None:
    for hit in outcome.hits:
        if hit.source_key.lower() != mapping.source.lower():
            continue
        if str(hit.doc_id).strip().upper() != mapping.source_doc_id.strip().upper():
            continue
        title = _safe_retrieved_text(hit.title, limit=512)
        source = _safe_citation_component(hit.source_key, limit=32)
        doc_id = _safe_retrieved_text(hit.doc_id, limit=128)
        return title, f"[{source}:{doc_id}]"
    return None


def _safe_retrieved_text(value: str, *, limit: int) -> str:
    """Treat retrieved payload text as untrusted data before display."""
    return html.escape(str(value or "")[:limit], quote=False).replace("\n", " ")


def _safe_citation_component(value: str, *, limit: int) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(value or "")[:limit]) or "unknown"


def _render_cvss(
    cvss: CVSSAssessment,
    *,
    strip_cvss_metrics: bool = False,
    outcome: SearchOutcome | None = None,
) -> list[str]:
    if cvss.status == "exact" and cvss.score is not None and cvss.vector and cvss.severity:
        return [
            "**Technical severity**",
            f"- CVSS {cvss.version}: **{cvss.score:.1f} {cvss.severity.title()}**",
            f"- Vector: `{cvss.vector}`",
            f"- Basis: {_sanitize_model_text(cvss.rationale, strip_cvss_metrics=strip_cvss_metrics, outcome=outcome)}",
        ]
    if cvss.status == "range" and cvss.lower_bound and cvss.upper_bound:
        lower_assumptions = list(filter(None, (
            _sanitize_model_text(
                item, strip_cvss_metrics=strip_cvss_metrics, outcome=outcome
            ) for item in cvss.lower_bound.assumptions
        )))
        upper_assumptions = list(filter(None, (
            _sanitize_model_text(
                item, strip_cvss_metrics=strip_cvss_metrics, outcome=outcome
            ) for item in cvss.upper_bound.assumptions
        )))
        lines = [
            "**Technical severity range**",
            f"- Evidence-established: **{cvss.lower_bound.score:.1f} {cvss.lower_bound.severity.title()}** "
            f"`{cvss.lower_bound.vector}` - "
            f"{_sanitize_model_text(cvss.lower_bound.rationale, strip_cvss_metrics=strip_cvss_metrics, outcome=outcome)}",
            *(["  Assumptions: " + "; ".join(lower_assumptions)] if lower_assumptions else []),
            f"- Conditional upper scenario: **{cvss.upper_bound.score:.1f} {cvss.upper_bound.severity.title()}** "
            f"`{cvss.upper_bound.vector}` - "
            f"{_sanitize_model_text(cvss.upper_bound.rationale, strip_cvss_metrics=strip_cvss_metrics, outcome=outcome)}",
            *(["  Assumptions: " + "; ".join(upper_assumptions)] if upper_assumptions else []),
            *(
                ["- Unresolved metrics: " + "; ".join(filter(None, (
                    _sanitize_model_text(
                        item,
                        strip_cvss_metrics=strip_cvss_metrics,
                        outcome=outcome,
                    ) for item in cvss.unresolved_metrics
                )))]
                if cvss.unresolved_metrics else []
            ),
        ]
        return lines
    if cvss.status == "not_applicable":
        return [
            "**Technical severity**",
            f"- CVSS not applicable. "
            f"{_sanitize_model_text(cvss.rationale, strip_cvss_metrics=strip_cvss_metrics, outcome=outcome)}".strip(),
        ]
    pending = [
        "**Technical severity**",
        f"- Pending evidence. "
        f"{_sanitize_model_text(cvss.rationale, strip_cvss_metrics=strip_cvss_metrics, outcome=outcome)}".strip(),
    ]
    unresolved = list(filter(None, (
        _sanitize_model_text(
            item, strip_cvss_metrics=strip_cvss_metrics, outcome=outcome
        ) for item in cvss.unresolved_metrics
    )))
    if unresolved:
        pending.append("- Needed: " + "; ".join(unresolved))
    return pending


def _unsafe_model_fragment(value: str) -> bool:
    return bool(
        _AUTHORITATIVE_TOKEN.search(value)
        or _INLINE_CVSS_SCORE.search(value)
        or _INLINE_SEVERITY_LABEL.search(value)
        or _LEAKED_PLACEHOLDER.search(value)
        or _MODEL_CVSS_RANGE.search(value)
        or _KB_REFERENCE.search(value)
    )


def _cvss_metric_names(value: str) -> set[str]:
    names: set[str] = set()
    for match in _CVSS_METRIC_TOKEN.finditer(str(value or "")):
        names.update(name for name, token in match.groupdict().items() if token)
    return names


def _cvss_label_names(value: str) -> set[str]:
    aliases = {
        "attack vector": "AV", "vecteur d'attaque": "AV", "vecteur d’attaque": "AV",
        "attack complexity": "AC", "complexité": "AC", "complexite": "AC",
        "privilege required": "PR", "privileges required": "PR",
        "privilège requis": "PR", "privilèges requis": "PR",
        "user interaction": "UI", "interaction utilisateur": "UI",
        "scope": "S", "portée": "S", "portee": "S",
        "confidentiality": "C", "confidentialité": "C", "confidentialite": "C",
        "integrity": "I", "intégrité": "I", "integrite": "I",
        "availability": "A", "disponibilité": "A", "disponibilite": "A",
    }
    names = set()
    for match in _CVSS_LABEL_VALUE.finditer(str(value or "")):
        label = " ".join(match.group("label").lower().split())
        name = aliases.get(label)
        if name:
            names.add(name)
    return names


def _paragraph_has_itemized_cvss(value: str) -> bool:
    return len(_cvss_metric_names(value) | _cvss_label_names(value)) >= 2


def _model_authored_fields(draft: GroundedChatDraft) -> list[str]:
    values = [draft.answer_markdown, *draft.limitations, *draft.assumptions]
    for mapping in draft.mappings:
        values.extend((mapping.rationale, mapping.evidence_basis))
    for claim in draft.claims:
        values.extend((claim.statement, claim.evidence_basis, *claim.conditions))
    values.append(draft.cvss.rationale)
    if draft.cvss.lower_bound:
        values.extend((draft.cvss.lower_bound.rationale, *draft.cvss.lower_bound.assumptions))
    if draft.cvss.upper_bound:
        values.extend((draft.cvss.upper_bound.rationale, *draft.cvss.upper_bound.assumptions))
    values.extend(draft.cvss.unresolved_metrics)
    return [str(value or "") for value in values]


def _draft_has_itemized_cvss(draft: GroundedChatDraft) -> bool:
    return any(
        _paragraph_has_itemized_cvss(paragraph)
        for value in _model_authored_fields(draft)
        for paragraph in _PARAGRAPH_SPLIT.split(value)
    )


def _vector_identity(vector: str) -> tuple[str, tuple[tuple[str, str], ...]] | None:
    vector = str(vector or "").strip().strip("`.,;() ")
    if not vector:
        return None
    try:
        canonical, _, _ = _calculated_cvss(vector.upper())
    except Exception:
        return None
    version, *metrics = canonical.upper().split("/")
    parsed = []
    for metric in metrics:
        key, separator, value = metric.partition(":")
        if not separator:
            return None
        parsed.append((key, value))
    return version, tuple(sorted(parsed))


def _sanitize_model_text(
    value: str,
    *,
    strip_cvss_metrics: bool = False,
    outcome: SearchOutcome | None = None,
) -> str:
    """Drop unsafe sentences instead of leaking diagnostic placeholders."""
    text = str(value or "")
    protected: dict[str, str] = {}
    if outcome is not None:
        for match in _KB_REFERENCE.finditer(text):
            index = int(match.group(1))
            if not 1 <= index <= len(outcome.hits):
                continue
            token = f"__SAFE_CITATION_{uuid.uuid4().hex}__"
            hit = outcome.hits[index - 1]
            source = _safe_citation_component(hit.source_key, limit=32)
            doc_id = _safe_citation_component(hit.doc_id, limit=128)
            protected[token] = f"[{source}:{doc_id}]"
            text = text.replace(match.group(0), token)
    clean_paragraphs: list[str] = []
    for paragraph in _PARAGRAPH_SPLIT.split(text):
        # The explicit flag is retained for API compatibility, but paragraph
        # locality wins: an itemized vector elsewhere must not delete an
        # isolated metric from this paragraph.
        itemized_cvss = _paragraph_has_itemized_cvss(paragraph)
        clean_lines: list[str] = []
        for raw_line in paragraph.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            prefix = ""
            body = line
            marker = re.match(r"^(#{1,6}\s+|[-*+]\s+|\d+\.\s+|>\s+)", line)
            if marker:
                prefix = marker.group(1)
                body = line[marker.end():]
            fragments = re.split(r"(?<=[.!?])\s+", body)
            kept = [
                fragment for fragment in fragments
                if fragment
                and not _unsafe_model_fragment(fragment)
                and not (
                    itemized_cvss
                    and (
                        _CVSS_METRIC_TOKEN.search(fragment)
                        or _CVSS_LABEL_VALUE.search(fragment)
                    )
                )
            ]
            if kept:
                clean_lines.append(prefix + " ".join(kept))
        if clean_lines:
            clean_paragraphs.append("\n".join(clean_lines))
    result = "\n\n".join(clean_paragraphs)
    for token, citation in protected.items():
        result = result.replace(token, citation)
    # A model may forge canonical-looking citations or our internal token shape.
    allowed = set(protected.values())
    for citation in _CANONICAL_CITATION.findall(result):
        if citation not in allowed:
            result = result.replace(citation, "")
    result = _INTERNAL_CITATION_TOKEN.sub("", result)
    return result.strip()


def _identifier_status(identifier: str, outcome: SearchOutcome) -> str:
    expected_sources = (
        {"nvd"} if identifier.startswith("CVE-")
        else {"mitre"} if re.fullmatch(r"T\d{4}(?:\.\d{3})?", identifier)
        else {"owasp"} if re.fullmatch(r"A(?:0[1-9]|10):20\d{2}", identifier)
        else {"nvd", "owasp", "owasp_docs"} if identifier.startswith("CWE-")
        else {"finding_templates"}
    )
    for hit in outcome.hits:
        aliases = {str(hit.doc_id).strip().upper()}
        payload = hit.payload or {}
        for key in (
            "template_code", "cve_id", "technique_id", "attack_id", "cwe", "cwe_ids"
        ):
            value = payload.get(key)
            if isinstance(value, list):
                aliases.update(_normalize_identifier_alias(item, key) for item in value)
            elif value:
                aliases.add(_normalize_identifier_alias(value, key))
        if hit.source_key.lower() in expected_sources and identifier in aliases:
            return f"retrieved as [{hit.source_key}:{hit.doc_id}]"
    reports = [item for item in outcome.reports if item.source_key.lower() in expected_sources]
    if not reports:
        names = ", ".join(sorted(expected_sources))
        return f"not verified; none of the authoritative sources ({names}) were part of this retrieval"
    healthy = [item for item in reports if not item.is_failure]
    if not healthy:
        detail = ", ".join(
            f"{item.display_name} was {item.availability.value}" for item in reports
        )
        return f"not verified; {detail}"
    return "no exact authoritative record was retrieved this turn"


def _normalize_identifier_alias(value, field: str) -> str:
    alias = str(value).strip().upper()
    if field in {"cwe", "cwe_ids"} and alias.isdigit():
        return f"CWE-{alias}"
    return alias


def render_grounded_response(result: GroundingResult, outcome: SearchOutcome) -> str:
    """Render authoritative fields from validated Python objects, never model prose."""
    draft = result.draft
    strip_cvss_metrics = result.itemized_cvss
    narrative = draft.answer_markdown.strip()
    narrative = _sanitize_model_text(
        narrative, strip_cvss_metrics=strip_cvss_metrics, outcome=outcome
    )

    lines = [narrative] if narrative else []
    applicable = [
        mapping for mapping in draft.mappings
        if str(mapping.applicability) in {"direct", "supporting", "conditional"}
    ]
    if applicable:
        lines += ["", "**Validated mappings**"]
        for mapping in applicable:
            canonical = _canonical_mapping(mapping, outcome)
            if canonical is None:
                continue
            title, citation = canonical
            lines.append(
                f"- **{mapping.identifier} - {title}** ({mapping.applicability}) {citation}: "
                f"{_sanitize_model_text(mapping.rationale, strip_cvss_metrics=strip_cvss_metrics, outcome=outcome)}"
            )

    lines += [
        "",
        *_render_cvss(
            draft.cvss,
            strip_cvss_metrics=strip_cvss_metrics,
            outcome=outcome,
        ),
    ]

    observed = [claim for claim in draft.claims if claim.level.value == "observed"]
    demonstrated = [
        claim for claim in draft.claims if claim.level.value == "logically_demonstrated"
    ]
    conditional = [claim for claim in draft.claims if claim.level.value == "conditional"]
    for heading, claims in (
        ("Observed", observed),
        ("Logically demonstrated", demonstrated),
        ("Conditional", conditional),
    ):
        if not claims:
            continue
        lines += ["", f"**{heading} claims**"]
        for claim in claims:
            statement = _sanitize_model_text(
                claim.statement, strip_cvss_metrics=strip_cvss_metrics
                , outcome=outcome
            )
            if not statement:
                continue
            suffix = (
                " Conditions: " + "; ".join(filter(None, (
                    _sanitize_model_text(
                        item, strip_cvss_metrics=strip_cvss_metrics
                        , outcome=outcome
                    ) for item in claim.conditions
                )))
                if any(_sanitize_model_text(
                    item, strip_cvss_metrics=strip_cvss_metrics
                    , outcome=outcome
                ) for item in claim.conditions) else ""
            )
            evidence = ", ".join(claim.evidence_ids) or "no admissible evidence reference"
            lines.append(f"- {statement} _Evidence: {evidence}._{suffix}")

    if result.requested_identifiers:
        lines += ["", "**Requested identifier checks**"]
        lines += [
            f"- **{identifier}**: {_identifier_status(identifier, outcome)}."
            for identifier in result.requested_identifiers
        ]

    if draft.limitations or draft.assumptions:
        lines += ["", "**Limits and assumptions**"]
        sanitized = [
            _sanitize_model_text(
                item, strip_cvss_metrics=strip_cvss_metrics, outcome=outcome
            )
            for item in [*draft.limitations, *draft.assumptions] if item
        ]
        lines += [f"- {item}" for item in sanitized if item]
    if result.issues:
        lines += [
            "",
            "**Grounding status**",
            "- Some generated proposals were rejected or conservatively rewritten by deterministic validation.",
        ]
    lines += ["", f"_{outcome.provenance_line()}_"]
    return "\n".join(lines).strip()


async def generate_structured_response(
    client: AsyncLLMClient,
    messages: Sequence[dict],
    outcome: SearchOutcome,
    *,
    model: str | None = None,
    text_evidence_count: int = 0,
    image_evidence_count: int = 0,
    stage: StageCallback | None = None,
) -> tuple[str, GroundingResult]:
    """One normal model call, one bounded correction call only when proof checks fail."""
    user_text = _current_user_text(messages)
    if stage:
        await stage("draft", "Drafting analysis", "active", "Building a structured evidence-based draft")
    try:
        raw = await client.generate_validation(
            system=CHAT_DRAFT_SYSTEM,
            user_message=build_draft_request(messages, outcome),
            max_tokens=5000,
            model=model,
            parse_attempts=1,
            json_schema=GroundedChatDraft.model_json_schema(),
        )
        draft, parse_issues = parse_draft(raw)
    except Exception as exc:
        # Model formatting and provider availability are both recoverable at
        # the system boundary. Do not expose invalid prose or make correctness
        # depend on one provider being reachable.
        malformed = isinstance(exc, json.JSONDecodeError)
        raw = {
            "status": (
                "unparseable structured draft" if malformed
                else "structured model unavailable"
            )
        }
        reason = (
            "The first model draft was not valid JSON and could not be trusted."
            if malformed else
            f"The model provider was unavailable ({type(exc).__name__}); deterministic fallback retained."
        )
        draft = _deterministic_fallback_draft(
            user_text,
            outcome,
            reason,
        )
        parse_issues = [
            "The first model draft was not valid JSON."
            if malformed else
            f"The model provider was unavailable ({type(exc).__name__})."
        ]
        used_deterministic_fallback = True
    else:
        used_deterministic_fallback = False
    if stage:
        await stage("draft", "Drafting analysis", "complete", "Structured draft received")
        await stage("validate", "Validating facts", "active", "Checking citations, mappings, evidence and CVSS")
    result = validate_draft(
        draft,
        outcome,
        user_text=user_text,
        text_evidence_count=text_evidence_count,
        image_evidence_count=image_evidence_count,
    )
    result.issues = list(dict.fromkeys([*parse_issues, *result.issues]))
    if used_deterministic_fallback:
        result.generation_status = "deterministic_fallback"
    elif _draft_is_substantively_empty(result.draft):
        result.draft = _normalize_draft(
            _deterministic_fallback_draft(
                user_text,
                outcome,
                "The model returned a structurally valid but substantively empty draft.",
            ),
            outcome,
            user_text=user_text,
            text_evidence_count=text_evidence_count,
            image_evidence_count=image_evidence_count,
        )
        result.issues = list(dict.fromkeys([
            *result.issues,
            "The model draft did not meet the minimum useful-content requirement.",
        ]))
        result.generation_status = "deterministic_fallback"
    if stage:
        await stage(
            "validate", "Validating facts", "complete",
            "Validation passed" if not result.issues else f"Found {len(result.issues)} issue(s)",
        )

    if result.issues:
        if stage:
            await stage("correct", "Correcting draft", "active", "Requesting one bounded correction")
        try:
            revised_raw = await client.generate_validation(
                system=CHAT_DRAFT_SYSTEM,
                user_message=(
                    build_draft_request(messages, outcome)
                    + "\n\nPREVIOUS DRAFT:\n"
                    + json.dumps(raw, ensure_ascii=False)
                    + "\n\n"
                    + revision_request(result, outcome)
                ),
                max_tokens=5000,
                model=model,
                parse_attempts=1,
                json_schema=GroundedChatDraft.model_json_schema(),
            )
            revised, revised_parse_issues = parse_draft(revised_raw)
            revised_result = validate_draft(
                revised,
                outcome,
                user_text=user_text,
                text_evidence_count=text_evidence_count,
                image_evidence_count=image_evidence_count,
            )
            revised_result.issues = list(dict.fromkeys([
                *revised_parse_issues, *revised_result.issues
            ]))
            if (
                revised_result.draft.answer_markdown.strip()
                and len(revised_result.issues) < len(result.issues)
            ):
                if not _draft_is_substantively_empty(revised_result.draft):
                    result = revised_result
                    result.generation_status = "corrected_model"
            else:
                result.issues = list(dict.fromkeys([
                    *result.issues,
                    "Correction did not improve deterministic validation; original draft retained.",
                ]))
        except Exception as exc:
            # Two malformed completions exhaust the bounded model budget. Keep
            # the already validated safe draft and let Python render only facts
            # it can prove from the request and retrieval outcome.
            result.issues = list(dict.fromkeys([
                *result.issues,
                f"Correction failed ({type(exc).__name__}); original safe fallback retained.",
            ]))
            if not result.draft.answer_markdown.strip():
                result.draft = _normalize_draft(
                    _deterministic_fallback_draft(
                        user_text,
                        outcome,
                        "The correction response was unavailable; deterministic fallback retained.",
                    ),
                    outcome,
                    user_text=user_text,
                    text_evidence_count=text_evidence_count,
                    image_evidence_count=image_evidence_count,
                )
        result.corrected = True
        if stage:
            await stage(
                "correct", "Correcting draft", "complete",
                "Correction validated" if not result.issues else "Unsafe values will be removed by Python",
            )

    if stage:
        await stage("finalize", "Finalizing response", "active", "Rendering immutable values from Python")
    response = render_grounded_response(result, outcome)
    if stage:
        await stage("finalize", "Finalizing response", "complete", "Grounded response ready")
    return response, result


async def generate_conversational_response(
    client: AsyncLLMClient,
    messages: Sequence[dict],
    outcome: SearchOutcome,
    *,
    model: str | None = None,
    text_evidence_count: int = 0,
    image_evidence_count: int = 0,
    stage: StageCallback | None = None,
) -> tuple[str, list[str]]:
    """Generate ordinary chat as text, then apply deterministic output cleanup.

    Conversational chat does not need a machine-readable contract. Explicit finding
    validation continues to use generate_structured_response above.
    """
    if stage:
        await stage("draft", "Drafting analysis", "active", "Generating a natural-language answer")
    raw = await client.generate(
        messages=[{"role": "user", "content": build_text_request(messages, outcome)}],
        system=CHAT_TEXT_SYSTEM,
        max_tokens=5000,
        model=model,
    )
    if stage:
        await stage("draft", "Drafting analysis", "complete", "Natural-language response received")
        await stage("finalize", "Finalizing response", "active", "Applying deterministic grounding cleanup")
    cleaned = _sanitize_model_text(raw, outcome=outcome)
    issues: list[str] = []
    if cleaned != str(raw or "").strip():
        issues.append("Some unsupported identifiers, citations, or authoritative values were removed.")
    if stage:
        await stage("finalize", "Finalizing response", "complete", "Grounded response ready")
    return cleaned, issues
