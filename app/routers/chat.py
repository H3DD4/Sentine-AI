"""
Chat router — fully async with SSE streaming support, plus the
conversational validation workflow (Option A: explicit trigger).

POST /chat                → standard JSON response, supports action="validate"/"generate_report"
POST /chat/with-evidence  → multipart variant, accepts files alongside the conversation
POST /chat/stream         → Server-Sent Events (pure conversation, no validation)
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.kb.base import SearchOutcome
from app.schemas import ChatRequest, ChatMessage, ValidationResult
from app.services.retrieval import multimodal_search
from app.services.llm_client import AsyncLLMClient
from app.services.validation import validate_finding
from app.services.evidence import parse_evidence
from app.services.report import generate_report_docx
from app.models import Finding, Evidence, Engagement
import json
import io
import logging

router = APIRouter(prefix="/chat", tags=["chat"])
log = logging.getLogger(__name__)

CHAT_SYSTEM = """You are RedTeam Assist, an internal AI assistant for Forvis Mazars red teamers.
You help analysts validate penetration testing findings, correlate CVEs,
map to MITRE ATT&CK, and prepare evidence for reporting.
Be concise, technical, and precise. Do not speculate beyond what evidence supports.
Do not generate exploit code or attack payloads.

Knowledge base context is supplied in a [KB CONTEXT] section. Each entry is labelled with the
source it came from. Ground your response in those entries and cite specific CVE or technique IDs.

The [DATA COVERAGE] line states which knowledge sources were searched for this turn and which
were unavailable. Honour it:
- Do not claim a source supports you unless entries from it appear in the context.
- If a source was unavailable, say so plainly when the question depended on it, rather than
  filling the gap from your own training data and presenting it as retrieved fact.
- If no context was retrieved at all, answer from general knowledge but state clearly that the
  answer is not grounded in the knowledge base."""


# ── Helpers ─────────────────────────────────────────────────────────────────

def _retrieval_inputs(messages: list[ChatMessage]) -> tuple[str, list[str]]:
    """
    Split a conversation into the query and its supporting context.

    The previous version embedded every user turn concatenated, which produced
    a vector averaging the analyst's current question with everything they had
    said before — so the longer the conversation, the less the current question
    influenced retrieval. The latest turn is the question; earlier turns are
    passed separately as their own retrieval arm, where they can contribute
    without diluting it.
    """
    user_turns = [m.content for m in messages if m.role == "user" and m.content.strip()]
    if not user_turns:
        return "", []
    latest = user_turns[-1]
    prior = user_turns[:-1]
    # Only the most recent prior turns carry useful context; older ones are
    # usually a different sub-topic and add noise.
    context = ["\n".join(prior[-3:])] if prior else []
    return latest, context


def _build_messages_with_context(
    messages: list[ChatMessage], outcome: SearchOutcome
) -> list[dict]:
    """Inject KB context as a final system section, NOT appended to user text."""
    out = [{"role": m.role, "content": m.content} for m in messages]

    lines = []
    for hit in outcome.hits:
        payload = hit.payload or {}
        cvss = f" (CVSS {payload['cvss_v3']})" if payload.get("cvss_v3") else ""
        # The source label rides on every line: without it the model cannot
        # tell a public CVE record from a colleague's prior engagement finding,
        # and neither can the analyst reading the citation afterwards.
        lines.append(
            f"• [{hit.source_label}] {hit.title}{cvss}: {hit.text[:300]}"
        )

    # The coverage line is always injected, including when nothing was found —
    # that is precisely the case where the model would otherwise answer from
    # memory and the analyst would have no way to tell.
    context_block = "[DATA COVERAGE] " + outcome.provenance_line()
    if lines:
        context_block += "\n\n[KB CONTEXT — use this to inform your response]\n" + "\n".join(lines)
    else:
        context_block += "\n\n[KB CONTEXT] No knowledge base entries matched this query."

    user_idx = next(
        (i for i in reversed(range(len(out))) if out[i]["role"] == "user"),
        None,
    )
    if user_idx is not None:
        out.insert(user_idx, {"role": "user", "content": context_block})
        out.insert(user_idx + 1, {
            "role": "assistant",
            "content": "Understood. I'll ground my analysis in that context and respect the stated coverage.",
        })

    return out


def _append_evidence_note(
    messages: list[ChatMessage], evidence_texts: list[str], image_descriptions: list[str]
) -> list[ChatMessage]:
    """Fold uploaded evidence into the last user turn so plain conversation (pre-validate) can reference it."""
    if not evidence_texts and not image_descriptions:
        return messages

    parts = []
    if evidence_texts:
        parts.append("=== ATTACHED EVIDENCE (text/log) ===\n" + "\n---\n".join(evidence_texts))
    if image_descriptions:
        parts.append("=== ATTACHED EVIDENCE (screenshots) ===\n" + "\n---\n".join(image_descriptions))
    note = "\n\n".join(parts)

    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if out[i].role == "user":
            out[i] = ChatMessage(role="user", content=out[i].content + "\n\n" + note)
            break
    return out


def _provenance_payload(outcome: SearchOutcome) -> dict:
    """
    The source-attribution block attached to every chat response.

    Shared by all three endpoints so the JSON, multipart and SSE paths cannot
    drift into describing coverage differently for the same query.
    """
    data = outcome.to_dict()
    return {
        "sources": data["sources"],
        "sources_used": data["sources_used"],
        "provenance": data["provenance"],
        "degraded": data["degraded"],
        "citations": data["results"],
    }


def _derive_title(text: str, limit: int = 80) -> str:
    """Fallback title when the pentester doesn't give one explicitly — no form, just talk."""
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "Untitled Finding")
    return (first_line[: limit - 1] + "…") if len(first_line) > limit else first_line


def _format_validation_message(
    result: ValidationResult, outcome: SearchOutcome | None = None
) -> str:
    """Turn a ValidationResult into a readable chat-style reply."""
    verdict_label = result.verdict.value.replace("_", " ").title()
    lines = [f"**Verdict:** {verdict_label} (confidence: {result.confidence:.0%})", "", result.reasoning]

    if result.matched_cves:
        lines += ["", "**Matched CVEs:** " + ", ".join(result.matched_cves)]
    if result.matched_techniques:
        lines += ["**MITRE Techniques:** " + ", ".join(result.matched_techniques)]
    if result.missing_evidence:
        lines += ["", "**Missing evidence:**"] + [f"- {m}" for m in result.missing_evidence]
    if result.recommended_next_steps:
        lines += ["", "**Recommended next steps:**"] + [f"- {s}" for s in result.recommended_next_steps]

    # The verdict travels with its provenance. This message is what an analyst
    # copies into a report, so the coverage statement has to be part of it
    # rather than living only in a UI panel they may not have looked at.
    if outcome is not None:
        lines += ["", f"_{outcome.provenance_line()}_"]

    return "\n".join(lines)


async def _persist_finding(
    title: str, description: str, result: ValidationResult, session: AsyncSession
) -> Finding:
    finding = Finding(
        title=title,
        description=description,
        verdict=result.verdict.value,   # NOTE: switch to result.verdict if your column is a native Enum type
        confidence=result.confidence,
        reasoning=result.reasoning,
        matched_cves=result.matched_cves,
        matched_techniques=result.matched_techniques,
        missing_evidence=result.missing_evidence,
        recommended_next_steps=result.recommended_next_steps,
    )
    session.add(finding)
    await session.flush()  # populate finding.id before we attach evidence rows / return it
    return finding


async def _run_validation(
    title: str | None, description: str, evidence_texts: list[str],
    image_descriptions: list[str], session: AsyncSession,
) -> tuple[Finding, ValidationResult, SearchOutcome]:
    try:
        result, outcome = await validate_finding(
            description, evidence_texts, image_descriptions, session
        )
    except Exception as exc:
        log.error("Validation failed: %s", exc)
        raise HTTPException(502, f"Validation failed: {exc}") from exc

    finding = await _persist_finding(title or _derive_title(description), description, result, session)
    return finding, result, outcome


def _safe_filename(name: str) -> str:
    """Strip anything that isn't filename-safe before using it in Content-Disposition."""
    keep = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)
    return keep[:80] or "report"


# ── Standard JSON endpoint ───────────────────────────────────────────────────

@router.post("")
async def chat(req: ChatRequest, session: AsyncSession = Depends(get_session)):
    """Standard non-streaming chat endpoint. Also handles action="validate"/"generate_report"."""

    if req.action == "validate":
        full_description = "\n".join(m.content for m in req.messages if m.role == "user")
        finding, result, outcome = await _run_validation(req.title, full_description, [], [], session)
        await session.commit()
        await session.refresh(finding)
        return {
            "response": _format_validation_message(result, outcome),
            "finding_id": finding.id,
            "verdict": result.verdict,
            "confidence": result.confidence,
            **_provenance_payload(outcome),
        }

    if req.action == "generate_report":
        if not req.finding_id:
            raise HTTPException(400, "finding_id is required to generate a report")
        finding = await session.get(Finding, req.finding_id)
        if finding is None:
            raise HTTPException(404, "Finding not found")

        # NOTE: assumes a single finding per report for now. If you later want
        # "generate report for all findings in an engagement", swap this for a
        # query against Finding.engagement_id and pass the full list below.
        engagement_title = req.title or finding.title

        # The client name comes from the engagement. The previous code read
        # `finding.client_name`, an attribute Finding has never had, so the
        # `or` fallback fired every time and every report was addressed to
        # "Forvis Mazars" — the firm writing the report rather than the client
        # receiving it. Left unset when there is no engagement, so the gap is
        # visible in the document instead of being papered over.
        client_name = "Unspecified client"
        if finding.engagement_id:
            engagement = await session.get(Engagement, finding.engagement_id)
            if engagement and engagement.client_name:
                client_name = engagement.client_name

        try:
            docx_bytes = await generate_report_docx(
                findings=[finding],
                engagement_title=engagement_title,
                client_name=client_name,
            )
        except Exception as exc:
            log.error("Report generation failed: %s", exc)
            raise HTTPException(502, f"Report generation failed: {exc}") from exc

        filename = f"{_safe_filename(finding.title)}_report.docx"

        return StreamingResponse(
            io.BytesIO(docx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # normal conversational path
    query, prior_turns = _retrieval_inputs(req.messages)
    outcome = await multimodal_search(query, session, evidence_texts=prior_turns)
    messages = _build_messages_with_context(req.messages, outcome)

    client = AsyncLLMClient()
    response = await client.generate(messages=messages, system=CHAT_SYSTEM, max_tokens=1200)
    return {"response": response, **_provenance_payload(outcome)}


# ── Multipart endpoint (evidence uploads) ───────────────────────────────────

@router.post("/with-evidence")
async def chat_with_evidence(
    messages: str = Form(...),          # JSON-encoded list of {"role", "content"}
    action: str | None = Form(None),    # "validate" or None (plain conversation with evidence attached)
    title: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    session: AsyncSession = Depends(get_session),
):
    try:
        parsed_messages = [ChatMessage(**m) for m in json.loads(messages)]
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"Invalid messages payload: {exc}")

    evidence_texts, image_descriptions, parsed_files = [], [], []
    for upload in files:
        file_bytes = await upload.read()
        parsed = await parse_evidence(upload.filename or "unknown", file_bytes)
        if parsed.get("extracted_text"):
            evidence_texts.append(parsed["extracted_text"])
        if parsed.get("image_description"):
            image_descriptions.append(parsed["image_description"])
        parsed_files.append(parsed)

    full_description = "\n".join(m.content for m in parsed_messages if m.role == "user")

    if action == "validate":
        finding, result, outcome = await _run_validation(title, full_description, evidence_texts, image_descriptions, session)

        for f in parsed_files:
            session.add(Evidence(
                finding_id=finding.id,
                filename=f["filename"],
                file_type=f["file_type"],
                storage_path=f["storage_path"],
                extracted_text=f.get("extracted_text"),
                image_description=f.get("image_description"),
            ))

        await session.commit()
        await session.refresh(finding)
        return {
            "response": _format_validation_message(result, outcome),
            "finding_id": finding.id,
            "verdict": result.verdict,
            "confidence": result.confidence,
            **_provenance_payload(outcome),
        }

    # plain conversation with evidence attached, not validating yet
    parsed_messages = _append_evidence_note(parsed_messages, evidence_texts, image_descriptions)
    query, prior_turns = _retrieval_inputs(parsed_messages)
    # Uploaded evidence is searched as its own arm rather than being folded
    # into the question — a log excerpt and a question are different kinds of
    # text and embed to different places.
    outcome = await multimodal_search(
        query,
        session,
        evidence_texts=[*prior_turns, *evidence_texts],
        image_descriptions=image_descriptions,
    )
    llm_messages = _build_messages_with_context(parsed_messages, outcome)

    client = AsyncLLMClient()
    response = await client.generate(messages=llm_messages, system=CHAT_SYSTEM, max_tokens=1200)
    return {"response": response, **_provenance_payload(outcome)}


# ── Streaming endpoint (unchanged — pure conversation) ──────────────────────

@router.post("/stream")
async def chat_stream(req: ChatRequest, session: AsyncSession = Depends(get_session)):
    """
    SSE streaming chat endpoint.
    Response is text/event-stream with `data: <json>\n\n` frames.

    Frame order is part of the contract:
      1. {"sources": [...], "provenance": "...", "degraded": bool}
      2. {"token": "<chunk>"}  (repeated)
      3. {"done": true}

    The sources frame is emitted first, before any token, so the analyst sees
    which corpora the answer is standing on while it is still being written —
    rather than reading a confident paragraph and only afterwards learning
    that the CVE feed was down.
    """
    query, prior_turns = _retrieval_inputs(req.messages)
    outcome = await multimodal_search(query, session, evidence_texts=prior_turns)
    messages = _build_messages_with_context(req.messages, outcome)

    async def event_generator():
        yield f"data: {json.dumps(_provenance_payload(outcome))}\n\n"

        client = AsyncLLMClient()
        try:
            async for chunk in client.generate_stream(
                messages=messages,
                system=CHAT_SYSTEM,
                max_tokens=1200,
            ):
                yield f"data: {json.dumps({'token': chunk})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        finally:
            yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )