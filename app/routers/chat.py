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
from app.services.retrieval import federated_search, multimodal_search
from app.services.llm_client import AsyncLLMClient, configured_chat_models
from app.services.validation import impact_narrative, validate_finding
from app.services.chat_grounding import generate_conversational_response
from app.config import settings
from app.services.upload_processing import (
    cleanup_staged_evidence,
    move_staged_evidence,
    stage_evidence_uploads,
)
from app.services.report import generate_report_docx
from app.models import Finding, Evidence, Engagement
import json
import io
import logging
import asyncio
import time

router = APIRouter(prefix="/chat", tags=["chat"])
log = logging.getLogger(__name__)
SSE_PIPELINE_POLL_SECONDS = 1.0
SSE_KEEPALIVE_SECONDS = 10.0


def _validate_chat_model(model: str | None) -> None:
    if model and model not in configured_chat_models():
        raise HTTPException(400, "Selected model is not in the configured chat model chain")

def _needs_retrieval(query: str) -> bool:
    """Avoid attaching arbitrary CVEs to greetings and other social-only turns."""
    normalized = " ".join(query.lower().strip().split()).strip(".!?,")
    social_turns = {
        "hello", "hi", "hey", "good morning", "good afternoon", "good evening",
        "thanks", "thank you", "ok", "okay",
    }
    return bool(normalized) and normalized not in social_turns


def _social_reply(query: str) -> str | None:
    if _needs_retrieval(query):
        return None
    return (
        "Hello. Describe the finding or provide technical evidence, and I can help structure "
        "the validation, CVSS rationale, impact, CVE correlation, and ATT&CK mapping."
    )


async def _conversation_outcome(
    query: str, prior_turns: list[str], session: AsyncSession
) -> SearchOutcome:
    if not _needs_retrieval(query):
        return SearchOutcome(query=query)
    retrieval_query = query
    if prior_turns:
        retrieval_query = f"{query}\n\nPrior finding context:\n{prior_turns[-1]}"
    try:
        return await asyncio.wait_for(
            federated_search(retrieval_query, session),
            timeout=45,
        )
    except asyncio.TimeoutError:
        log.warning("Chat retrieval timed out; continuing without KB context")
        return SearchOutcome(
            query=query,
            degraded=True,
            notes=["knowledge retrieval timed out; the response is not grounded in the local KB"],
        )
    except Exception as exc:
        log.exception("Chat retrieval failed; continuing without KB context")
        return SearchOutcome(
            query=query,
            degraded=True,
            notes=[
                f"knowledge retrieval failed ({type(exc).__name__}); "
                "the response is not grounded in the local KB"
            ],
        )


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


def _bounded_provider_messages(
    messages: list[ChatMessage], *, max_messages: int = 24, max_chars: int = 48_000
) -> list[dict]:
    """Keep recent valid turns within a provider-safe context budget."""
    selected: list[dict] = []
    remaining = max_chars
    for message in reversed(messages):
        if message.role not in ("user", "assistant"):
            continue
        content = message.content
        if not content.strip():
            continue
        if not selected and len(content) > remaining:
            content = content[:remaining]
        elif len(content) > remaining:
            break
        selected.append({"role": message.role, "content": content})
        remaining -= len(content)
        if len(selected) >= max_messages or remaining <= 0:
            break
    selected.reverse()
    while selected and selected[0]["role"] == "assistant":
        selected.pop(0)
    return selected


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


async def _generate_complete_response(
    client: AsyncLLMClient,
    messages: list[dict],
    *,
    system: str,
    model: str | None = None,
) -> str:
    """Continue only provider-confirmed length truncations, without repetition."""
    completed = ""
    round_messages = list(messages)
    for continuation in range(settings.CHAT_MAX_CONTINUATIONS + 1):
        generation_args = dict(
            messages=round_messages,
            system=system,
            max_tokens=settings.CHAT_MAX_TOKENS,
        )
        if model is not None:
            generation_args["model"] = model
        part = await client.generate(**generation_args)
        completed += part
        if not _was_length_limited(client.last_finish_reason):
            return completed
        if continuation == settings.CHAT_MAX_CONTINUATIONS:
            break
        round_messages = [
            *messages,
            {"role": "assistant", "content": completed},
            {
                "role": "user",
                "content": (
                    "Continue exactly where the response stopped. Do not repeat any heading, "
                    "table row, sentence, or source list already written. Finish all remaining "
                    "requested sections."
                ),
            },
        ]
    return completed


def _was_length_limited(reason: str | None) -> bool:
    return str(reason or "").lower() in {"length", "max_tokens", "max_output_tokens"}


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
    impact = result.impact_assessment
    if impact.demonstrated_capability:
        lines += ["", "**Demonstrated capability:** " + impact.demonstrated_capability]
    if impact.technical_impact:
        lines += ["", "**Technical impact:** " + impact.technical_impact]
    if impact.business_impact:
        lines += ["", "**Business impact:** " + impact.business_impact]
    priority = impact.business_priority.value.replace("_", " ").title()
    lines += ["", f"**Business priority:** {priority}"]
    if impact.priority_rationale:
        lines.append(impact.priority_rationale)
    if impact.cvss.status == "exact":
        lines += [
            "",
            f"**Technical severity (CVSS {impact.cvss.version}):** "
            f"{impact.cvss.score:.1f} {impact.cvss.severity.title()} — `{impact.cvss.vector}`",
        ]
        if impact.cvss.rationale:
            lines.append(impact.cvss.rationale)
    elif impact.cvss.status == "range" and impact.cvss.lower_bound and impact.cvss.upper_bound:
        lines += [
            "",
            f"**Technical severity range (CVSS {impact.cvss.version}):** "
            f"{impact.cvss.lower_bound.score:.1f}–{impact.cvss.upper_bound.score:.1f}",
            f"- Evidence-established: `{impact.cvss.lower_bound.vector}` — {impact.cvss.lower_bound.rationale}",
            f"- Conditional upper scenario: `{impact.cvss.upper_bound.vector}` — {impact.cvss.upper_bound.rationale}",
        ]
        if impact.cvss.unresolved_metrics:
            lines += ["- Unresolved: " + "; ".join(impact.cvss.unresolved_metrics)]
    elif impact.cvss.rationale:
        lines += ["", "**CVSS:** Pending evidence. " + impact.cvss.rationale]
    applicable_mappings = [
        mapping for mapping in result.mappings
        if mapping.applicability in {"direct", "supporting", "conditional"}
    ]
    rejected_mappings = [
        mapping for mapping in result.mappings
        if mapping.applicability in {"rejected", "unsupported"}
    ]
    if applicable_mappings:
        lines += ["", "**Evidence-graded mappings:**"] + [
            f"- {mapping.identifier}: {mapping.applicability.replace('_', ' ')} — {mapping.rationale}"
            for mapping in applicable_mappings
        ]
    if rejected_mappings:
        lines += ["", "**Rejected or unsupported mappings:**"] + [
            f"- {mapping.identifier}: {mapping.rationale}"
            for mapping in rejected_mappings
        ]
    if impact.excluded_claims:
        lines += ["", "**Not established:**"] + [f"- {claim}" for claim in impact.excluded_claims]
    if impact.clarification_questions:
        lines += ["", "**To finalize business impact, answer these together:**"]
        for index, item in enumerate(impact.clarification_questions, 1):
            options = f" Options: {', '.join(item.answer_options)}." if item.answer_options else ""
            lines.append(f"{index}. {item.question}{options} ({item.why_it_matters})")
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
    cvss = result.impact_assessment.cvss
    exact_cvss = cvss.status == "exact"
    finding = Finding(
        title=title,
        description=description,
        verdict=result.verdict.value,   # NOTE: switch to result.verdict if your column is a native Enum type
        confidence=result.confidence,
        reasoning=result.reasoning,
        impact=impact_narrative(result),
        impact_assessment=result.impact_assessment.model_dump(mode="json"),
        severity=cvss.severity if exact_cvss else "",
        cvss_score=cvss.score if exact_cvss else None,
        cvss_vector=cvss.vector if exact_cvss else "",
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
    _validate_chat_model(req.model)
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
            "impact_assessment": result.impact_assessment.model_dump(mode="json"),
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
    outcome = await _conversation_outcome(query, prior_turns, session)
    social_reply = _social_reply(query)
    if social_reply:
        return {"response": social_reply, **_provenance_payload(outcome)}
    messages = _bounded_provider_messages(req.messages)

    client = AsyncLLMClient()
    response, grounding_issues = await generate_conversational_response(
        client, messages, outcome, model=req.model
    )
    return {
        "response": response,
        "generation": client.last_generation,
        "grounding_issues": grounding_issues,
        "corrected": False,
        **_provenance_payload(outcome),
    }


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

    parsed_files, processing = await stage_evidence_uploads(files)
    evidence_texts = [p["analysis_text"] for p in parsed_files if p.get("analysis_text")]
    image_descriptions = [
        p["image_description"] for p in parsed_files if p["image_description"]
    ]

    full_description = "\n".join(m.content for m in parsed_messages if m.role == "user")

    if action == "validate":
        try:
            finding, result, outcome = await _run_validation(
                title, full_description, evidence_texts, image_descriptions, session
            )
        except Exception:
            cleanup_staged_evidence(parsed_files)
            raise

        try:
            move_staged_evidence(parsed_files, finding.id)
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
        except Exception:
            cleanup_staged_evidence(parsed_files)
            await session.rollback()
            raise
        await session.refresh(finding)
        return {
            "response": _format_validation_message(result, outcome),
            "finding_id": finding.id,
            "verdict": result.verdict,
            "confidence": result.confidence,
            "impact_assessment": result.impact_assessment.model_dump(mode="json"),
            **_provenance_payload(outcome),
            "processing": processing,
        }

    # plain conversation with evidence attached, not validating yet
    parsed_messages = _append_evidence_note(parsed_messages, evidence_texts, image_descriptions)
    query, prior_turns = _retrieval_inputs(parsed_messages)
    # Uploaded evidence is searched as its own arm rather than being folded
    # into the question — a log excerpt and a question are different kinds of
    # text and embed to different places.
    try:
        outcome = await multimodal_search(
            query,
            session,
            evidence_texts=[*prior_turns, *evidence_texts],
            image_descriptions=image_descriptions,
        )
    except Exception:
        cleanup_staged_evidence(parsed_files)
        raise
    llm_messages = _bounded_provider_messages(parsed_messages)

    client = AsyncLLMClient()
    try:
        response, grounding_issues = await generate_conversational_response(
            client,
            llm_messages,
            outcome,
            model=None,
            text_evidence_count=len(evidence_texts),
            image_evidence_count=len(image_descriptions),
        )
    finally:
        cleanup_staged_evidence(parsed_files)
    return {
        "response": response,
        "generation": client.last_generation,
        "grounding_issues": grounding_issues,
        "corrected": False,
        **_provenance_payload(outcome),
        "processing": processing,
    }


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
    _validate_chat_model(req.model)
    query, prior_turns = _retrieval_inputs(req.messages)
    social_reply = _social_reply(query)

    async def event_generator():
        yield f"data: {json.dumps({'stage': {'key': 'retrieve', 'label': 'Searching knowledge', 'status': 'active', 'detail': 'Checking approved local sources'}})}\n\n"
        outcome = await _conversation_outcome(query, prior_turns, session)
        messages = _bounded_provider_messages(req.messages)
        yield f"data: {json.dumps(_provenance_payload(outcome))}\n\n"
        yield f"data: {json.dumps({'stage': {'key': 'retrieve', 'label': 'Searching knowledge', 'status': 'complete', 'detail': outcome.provenance_line()}})}\n\n"

        if social_reply:
            yield f"data: {json.dumps({'token': social_reply})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
            return

        client = AsyncLLMClient()
        try:
            stages: asyncio.Queue[dict] = asyncio.Queue()

            async def emit_stage(key: str, label: str, status: str, detail: str) -> None:
                await stages.put({
                    "key": key,
                    "label": label,
                    "status": status,
                    "detail": detail,
                })

            generation_task = asyncio.create_task(generate_conversational_response(
                client, messages, outcome, model=req.model, stage=emit_stage
            ))
            last_keepalive = time.monotonic()
            try:
                while not generation_task.done() or not stages.empty():
                    try:
                        stage_data = await asyncio.wait_for(
                            stages.get(), timeout=SSE_PIPELINE_POLL_SECONDS
                        )
                        yield f"data: {json.dumps({'stage': stage_data})}\n\n"
                    except asyncio.TimeoutError:
                        now = time.monotonic()
                        if now - last_keepalive >= SSE_KEEPALIVE_SECONDS:
                            # SSE comments are ignored by the client parser but
                            # keep browsers and reverse proxies from declaring a
                            # private validation/correction call stalled.
                            yield ": grounding pipeline active\n\n"
                            last_keepalive = now
                completed, grounding_issues = await generation_task
            finally:
                if not generation_task.done():
                    generation_task.cancel()
                    try:
                        await generation_task
                    except asyncio.CancelledError:
                        pass
            if client.last_generation:
                yield f"data: {json.dumps({'generation': client.last_generation})}\n\n"
            if grounding_issues:
                yield f"data: {json.dumps({'grounding_issues': grounding_issues})}\n\n"
            # Buffering is deliberate: no unaudited token is exposed and then
            # impossible to retract. Moderate chunks retain incremental UI rendering.
            for start in range(0, len(completed), 240):
                yield f"data: {json.dumps({'token': completed[start:start + 240]})}\n\n"
        except Exception as exc:
            log.exception("Grounded streaming chat failed after retrieval")
            yield f"data: {json.dumps({'stage': {'key': 'pipeline', 'label': 'Grounding failed', 'status': 'error', 'detail': type(exc).__name__}})}\n\n"
            yield f"data: {json.dumps({'error': 'Grounded response generation failed. The server logged the underlying ' + type(exc).__name__ + '.'})}\n\n"
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
