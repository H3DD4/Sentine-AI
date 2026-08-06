"""
Multimodal query planning.

The problem this solves
-----------------------
A finding arrives as several different kinds of text: the analyst's prose
description, raw tool output or log excerpts, and vision-extracted text from
screenshots.  The previous pipeline retrieved on the description alone and
ignored the rest, which meant the single most identifying string in a typical
finding — the version banner in a Nmap excerpt, the stack trace in a log, the
error dialog in a screenshot — never reached the retriever.

The obvious fix, concatenating everything into one query, is worse than it
looks for two reasons:

  * The embedding of a concatenation is a centroid.  A vector averaging
    "authentication bypass on the admin portal" with 400 lines of Nmap output
    points at neither, which is the same failure that made the legacy
    mean-pooled document vectors useless.
  * BGE truncates at 512 tokens.  A long evidence dump silently pushes the
    analyst's actual description out of the window, so the one input that best
    states the question is the one most likely to be discarded.

So each modality becomes its own query, they run concurrently, and the result
lists are fused by rank.  Rank fusion is required rather than convenient here:
the similarity scores from a prose query and from a log-excerpt query are not
on a comparable scale, so averaging or thresholding them across queries would
be meaningless — the same reason the retriever fuses its dense and sparse arms
by rank instead of blending their scores.

Identifiers are handled separately.  A CVE or ATT&CK ID appearing anywhere in
any modality is extracted and issued as its own query, because an exact
identifier is a fact rather than a similarity judgement and should never be
left to depend on whether the surrounding prose happened to embed nearby.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.kb.base import Availability, RetrievalHit, SearchOutcome, SourceReport
from app.kb.registry import all_sources

log = logging.getLogger(__name__)

#: Upper bound on queries per request. Each one costs a dense embed, a sparse
#: embed and one Qdrant call per source; they run concurrently, so the cost is
#: mostly CPU on the embedder rather than wall-clock. Five keeps a worst-case
#: request well under a second on the embedding side while still covering a
#: description, two evidence excerpts and a screenshot.
MAX_QUERIES = 5

#: Evidence excerpts are truncated well below the model's 512-token window.
#: A long tool dump is mostly boilerplate — banners, column headers, repeated
#: "open/filtered" lines — and letting it fill the window dilutes the few
#: tokens that actually identify the software.
EVIDENCE_CHARS = 600
DESCRIPTION_CHARS = 1500

#: Relative influence of each modality in cross-query fusion. The analyst's
#: own description states the question and is trusted most; machine evidence
#: corroborates it; OCR/vision text is noisiest and weighted lowest.
WEIGHT_DESCRIPTION = 1.0
WEIGHT_EVIDENCE = 0.7
WEIGHT_IMAGE = 0.55
WEIGHT_IDENTIFIER = 1.2

RRF_K = 60

#: Lines that carry no retrieval signal but dominate tool output by volume.
_NOISE_LINE = re.compile(
    r"^\s*(?:[-=*_#]{3,}|\[\*\]\s*$|\d+/(?:tcp|udp)\s+(?:closed|filtered))",
    re.IGNORECASE,
)


@dataclass
class PlannedQuery:
    """One retrieval query plus why it exists, so provenance can explain it."""

    text: str
    weight: float
    modality: str  # description | evidence | image | identifier

    def __post_init__(self) -> None:
        self.text = self.text.strip()


def _condense(text: str, limit: int) -> str:
    """
    Strip the parts of tool output that cost tokens and carry no signal.

    Deliberately conservative: it drops separator rules and closed/filtered
    port lines, collapses blank runs, and truncates. It does not try to parse
    any specific tool's format, because guessing wrong would silently discard
    the evidence the analyst cared about.
    """
    lines = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if _NOISE_LINE.match(line):
            continue
        lines.append(line.strip())
    condensed = "\n".join(lines)
    if len(condensed) > limit:
        head = limit * 2 // 3
        condensed = condensed[:head] + "\n" + condensed[-(limit - head):]
    return condensed


def extract_identifiers(texts: Sequence[str]) -> list[str]:
    """Collect canonical IDs from every modality, deduplicated, order-stable."""
    found: dict[str, None] = {}
    for source in all_sources():
        if source.id_pattern is None:
            continue
        for text in texts:
            if not text:
                continue
            for raw in source.id_pattern.findall(text):
                found.setdefault(source.normalize_id(raw), None)
    return list(found)


def plan_queries(
    description: str,
    evidence_texts: Optional[Sequence[str]] = None,
    image_descriptions: Optional[Sequence[str]] = None,
) -> list[PlannedQuery]:
    """Turn a multimodal finding into a small set of focused queries."""
    evidence_texts = list(evidence_texts or [])
    image_descriptions = list(image_descriptions or [])
    queries: list[PlannedQuery] = []

    description = (description or "").strip()
    if description:
        queries.append(
            PlannedQuery(
                text=_condense(description, DESCRIPTION_CHARS),
                weight=WEIGHT_DESCRIPTION,
                modality="description",
            )
        )

    # Identifiers first among the remainder: an exact CVE or technique ID is
    # the highest-precision signal available and must not be crowded out by
    # the MAX_QUERIES cap.
    identifiers = extract_identifiers([description, *evidence_texts, *image_descriptions])
    if identifiers:
        queries.append(
            PlannedQuery(
                text=" ".join(identifiers[:10]),
                weight=WEIGHT_IDENTIFIER,
                modality="identifier",
            )
        )

    remaining = max(0, MAX_QUERIES - len(queries))
    evidence_slots = min(len(evidence_texts), remaining)
    selected_evidence = _evenly_select(evidence_texts, evidence_slots)
    remaining -= evidence_slots

    for text in selected_evidence:
        condensed = _condense(text, EVIDENCE_CHARS)
        if condensed:
            queries.append(
                PlannedQuery(text=condensed, weight=WEIGHT_EVIDENCE, modality="evidence")
            )

    for text in _evenly_select(image_descriptions, remaining):
        condensed = _condense(text, EVIDENCE_CHARS)
        if condensed:
            queries.append(
                PlannedQuery(text=condensed, weight=WEIGHT_IMAGE, modality="image")
            )

    # Drop near-duplicates: uploading the same log twice, or a screenshot whose
    # extracted text repeats the description, would otherwise let one piece of
    # evidence vote several times in the fusion.
    deduped: list[PlannedQuery] = []
    seen: set[str] = set()
    for q in queries:
        if not q.text:
            continue
        fingerprint = " ".join(q.text.lower().split())[:200]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(q)

    return deduped[:MAX_QUERIES]


def _evenly_select(values: Sequence[str], count: int) -> list[str]:
    """Select across the whole sequence so later files are not silently ignored."""
    values = [value for value in values if value and value.strip()]
    if count <= 0:
        return []
    if len(values) <= count:
        return values
    if count == 1:
        return [values[0]]
    indexes = [round(i * (len(values) - 1) / (count - 1)) for i in range(count)]
    return [values[index] for index in indexes]


def fuse_outcomes(
    outcomes: Sequence[tuple[PlannedQuery, SearchOutcome]],
    top_k: int,
) -> SearchOutcome:
    """
    Fuse per-query result lists into one ranked list, and merge the per-source
    provenance so the analyst still sees a single coherent coverage statement.
    """
    scores: dict[tuple[str, str], float] = {}
    best_hit: dict[tuple[str, str], RetrievalHit] = {}

    for planned, outcome in outcomes:
        for rank, hit in enumerate(outcome.hits):
            doc_key = (hit.source_key, hit.doc_id)
            scores[doc_key] = scores.get(doc_key, 0.0) + planned.weight / (RRF_K + rank)
            # Keep the best-scoring rendering of the document; a chunk that
            # matched the log excerpt may quote different text than the one
            # that matched the description, and the higher-ranked one is the
            # better thing to show and to cite.
            prev = best_hit.get(doc_key)
            if prev is None or hit.score > prev.score:
                best_hit[doc_key] = hit

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    hits: list[RetrievalHit] = []
    for i, (doc_key, score) in enumerate(ranked[:top_k]):
        hit = best_hit[doc_key]
        hit.score = score
        hit.rank = i
        hits.append(hit)

    reports = _merge_reports([o for _, o in outcomes], hits)
    return SearchOutcome(
        hits=hits,
        reports=reports,
        query=outcomes[0][0].text if outcomes else "",
        degraded=any(r.is_failure for r in reports),
    )


def _merge_reports(
    outcomes: Sequence[SearchOutcome], final_hits: Sequence[RetrievalHit]
) -> list[SourceReport]:
    """
    Collapse one report per source per query into one report per source.

    A source counts as available if *any* query reached it — a source that
    answered the description query but timed out on a log query is degraded,
    not down, and saying otherwise would misreport coverage. Hit counts are
    recomputed from the final fused list so the provenance line can never
    credit a source whose results were all outranked.
    """
    surviving: dict[str, int] = {}
    for h in final_hits:
        surviving[h.source_key] = surviving.get(h.source_key, 0) + 1

    merged: dict[str, SourceReport] = {}
    failures: dict[str, int] = {}
    attempts: dict[str, int] = {}

    for outcome in outcomes:
        for report in outcome.reports:
            attempts[report.source_key] = attempts.get(report.source_key, 0) + 1
            if report.is_failure:
                failures[report.source_key] = failures.get(report.source_key, 0) + 1

            existing = merged.get(report.source_key)
            if existing is None:
                merged[report.source_key] = SourceReport(
                    source_key=report.source_key,
                    display_name=report.display_name,
                    availability=report.availability,
                    latency_ms=report.latency_ms,
                    detail=report.detail,
                    searched_docs=report.searched_docs,
                )
                continue

            # Latency across concurrent queries is the slowest arm, not the sum.
            existing.latency_ms = max(existing.latency_ms, report.latency_ms)
            if _rank_state(report.availability) < _rank_state(existing.availability):
                existing.availability = report.availability
                existing.detail = report.detail

    for key, report in merged.items():
        report.hits = surviving.get(key, 0)
        if failures.get(key) and failures[key] < attempts.get(key, 1):
            # Reached on some queries but not all: coverage is real but partial,
            # and the analyst should know the evidence was searched unevenly.
            report.availability = Availability.DEGRADED
            report.detail = (
                f"answered {attempts[key] - failures[key]} of {attempts[key]} "
                "queries — some evidence was not searched against this source"
            )
        elif report.availability == Availability.OK and report.hits == 0:
            report.availability = Availability.NO_MATCH
            report.detail = "searched, no matching documents"

    return list(merged.values())


#: Better states sort first, so merging keeps the best outcome any query saw.
_STATE_ORDER = {
    Availability.OK: 0,
    Availability.DEGRADED: 1,
    Availability.NO_MATCH: 2,
    Availability.EMPTY: 3,
    Availability.DISABLED: 4,
    Availability.UNAVAILABLE: 5,
}


def _rank_state(state: Availability) -> int:
    return _STATE_ORDER.get(state, 9)
