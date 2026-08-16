"""
The KBSource contract.

Every knowledge source in Sentinel.AI implements this interface.  The retrieval
orchestrator knows *nothing* about NVD, MITRE or Ghostwriter specifically — it
only knows how to fan out over a list of KBSource objects, fuse their results,
and report which ones answered.  Adding a source therefore means writing one
adapter and registering it; no retrieval code changes.

Two product requirements are encoded directly in these types:

  1. "the app must show which data the RAG is using"
     → every result carries `source_key`/`source_label`, and every search
       returns a list of SourceReport describing what each arm did.

  2. "a rescue plan when a data source is not available — the agent needs to
      work in every scenario and that must be obvious to the user"
     → arms fail independently into an Availability state.  The orchestrator
       never raises when one source is down; it degrades and reports.
"""

from __future__ import annotations

import enum
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession


class Availability(str, enum.Enum):
    """What actually happened to one retrieval arm."""

    OK = "ok"                     # queried successfully, returned results
    NO_MATCH = "no_match"         # queried successfully, nothing passed threshold
    EMPTY = "empty"               # source is indexed but contains no documents
    DEGRADED = "degraded"         # answered, but Postgres/Qdrant counts disagree
    UNAVAILABLE = "unavailable"   # collection missing or backend error
    DISABLED = "disabled"         # switched off by an operator


#: Arms in these states contributed nothing, but are NOT errors the user must
#: act on — used to phrase the fallback notice correctly.
BENIGN_STATES = {Availability.NO_MATCH, Availability.EMPTY, Availability.DISABLED}


@dataclass
class SourceReport:
    """
    Per-arm outcome for a single search.  Surfaced to the analyst verbatim so
    they always know which corpora backed an answer — and which did not.
    """

    source_key: str
    display_name: str
    availability: Availability
    hits: int = 0
    latency_ms: int = 0
    detail: str = ""              # human-readable reason, shown in the UI
    searched_docs: Optional[int] = None

    @property
    def contributed(self) -> bool:
        return self.availability == Availability.OK and self.hits > 0

    @property
    def is_failure(self) -> bool:
        """True only for states that indicate something is actually broken."""
        return self.availability in (Availability.UNAVAILABLE, Availability.DEGRADED)

    def to_dict(self) -> dict:
        return {
            "source": self.source_key,
            "label": self.display_name,
            "status": self.availability.value,
            "hits": self.hits,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
            "searched_docs": self.searched_docs,
        }


@dataclass
class RetrievalHit:
    """
    One retrieved document, normalised across sources.

    `source_key` and `source_label` are mandatory and travel with the hit all
    the way into the LLM prompt and the UI citation list — a hit can never
    become anonymous context.
    """

    source_key: str
    source_label: str
    doc_id: str
    title: str
    text: str
    score: float
    payload: dict = field(default_factory=dict)
    url: Optional[str] = None
    rank: int = 0

    def to_dict(self) -> dict:
        return {
            "source": self.source_key,
            "source_label": self.source_label,
            "id": self.doc_id,
            "title": self.title,
            "description": self.text,
            "score": round(self.score, 4),
            "url": self.url,
            **self.payload,
        }


@dataclass
class SearchOutcome:
    """
    The complete result of a federated search: the fused hits plus the full
    provenance record of how they were obtained.
    """

    hits: list[RetrievalHit] = field(default_factory=list)
    reports: list[SourceReport] = field(default_factory=list)
    query: str = ""
    degraded: bool = False
    #: Non-source degradations — a ranking stage that could not run, an arm
    #: that dropped out. Kept separate from `reports` because these are not
    #: about *which corpus* answered but about *how well* it was ranked, and
    #: conflating them would make a missing reranker look like a dead source.
    notes: list[str] = field(default_factory=list)

    @property
    def sources_used(self) -> list[str]:
        return [r.source_key for r in self.reports if r.contributed]

    @property
    def sources_failed(self) -> list[SourceReport]:
        return [r for r in self.reports if r.is_failure]

    def provenance_line(self) -> str:
        """
        One-line, analyst-facing description of what backed this answer.
        Injected into the LLM prompt AND rendered in the UI, so the model and
        the human see the same claim about data coverage.
        """
        used = [r for r in self.reports if r.contributed]
        if used:
            parts = ", ".join(f"{r.display_name} ({r.hits})" for r in used)
            line = f"Knowledge sources used: {parts}."
        else:
            line = "Knowledge sources used: none — no indexed source returned a match."

        failed = self.sources_failed
        if failed:
            fparts = ", ".join(f"{r.display_name} — {r.detail or r.availability.value}" for r in failed)
            line += f" Unavailable this query: {fparts}. Answer is based only on the sources listed above."
        if self.notes:
            line += " Retrieval notes: " + "; ".join(self.notes) + "."
        return line

    def to_dict(self) -> dict:
        return {
            "results": [h.to_dict() for h in self.hits],
            "sources": [r.to_dict() for r in self.reports],
            "sources_used": self.sources_used,
            "degraded": self.degraded,
            "notes": self.notes,
            "provenance": self.provenance_line(),
        }


class KBSource(ABC):
    """
    Adapter for one knowledge source.

    Implementations are stateless singletons held in the registry.
    """

    #: Stable machine key. Also the suffix of the Qdrant collection.
    key: str = ""
    #: Human-facing name shown in the UI and in the LLM's provenance line.
    display_name: str = ""
    #: SQLAlchemy model backing this source.
    model: Any = None
    #: Fusion weight — how much this source's ranking counts in cross-source RRF.
    #: Firm-authored content outranks public feeds at equal rank, because a
    #: prior Ghostwriter write-up is more actionable than a raw CVE record.
    weight: float = 1.0
    #: Regex that recognises this source's canonical identifiers in free text
    #: (e.g. "CVE-2024-3094", "T1059.001"). Drives the exact-ID retrieval arm.
    id_pattern: Optional[re.Pattern] = None

    # ── Storage identity ─────────────────────────────────────────────────

    @property
    def collection(self) -> str:
        """Qdrant collection name — one collection per source."""
        return f"kb_{self.key}"

    @property
    def table(self) -> str:
        return self.model.__tablename__

    @property
    def exact_id_payload_field(self) -> str:
        """Payload field queried when this source recognizes a canonical ID."""
        return "doc_id"

    @property
    @abstractmethod
    def pk_column(self):
        """The model column holding the document's primary identifier."""

    # ── Indexing ─────────────────────────────────────────────────────────

    @abstractmethod
    def build_text(self, row: Any) -> str:
        """
        Produce the text that gets embedded.  This single method determines
        retrieval quality more than any other choice in the pipeline: it decides
        what the vector actually represents.
        """

    @abstractmethod
    def build_payload(self, row: Any) -> dict:
        """
        Payload stored alongside the vector.  Must contain enough to render a
        citation without a Postgres round-trip, so retrieval still degrades
        gracefully if the relational store is unreachable.
        """

    # ── Presentation ─────────────────────────────────────────────────────

    @abstractmethod
    def hit_from_payload(self, payload: dict, score: float) -> RetrievalHit:
        """Normalise a Qdrant payload into a source-tagged RetrievalHit."""

    def normalize_id(self, raw: str) -> str:
        """Canonicalise an identifier matched by `id_pattern`."""
        return raw.strip().upper()

    def extract_ids(self, text: str) -> list[str]:
        """Pull this source's canonical identifiers out of free text."""
        if not self.id_pattern or not text:
            return []
        seen: dict[str, None] = {}
        for m in self.id_pattern.finditer(text):
            seen.setdefault(self.normalize_id(m.group(0)), None)
        return list(seen)

    # ── Health ───────────────────────────────────────────────────────────

    async def row_count(self, session: AsyncSession) -> int:
        from sqlalchemy import func, select

        stmt = select(func.count()).select_from(self.model)
        return int((await session.execute(stmt)).scalar_one())

    async def unsynced_count(self, session: AsyncSession) -> int:
        """Rows in Postgres with no confirmed vector — direct drift measure."""
        from sqlalchemy import func, select

        stmt = (
            select(func.count())
            .select_from(self.model)
            .where(self.model.qdrant_synced_at.is_(None))
        )
        return int((await session.execute(stmt)).scalar_one())

    async def fetch_by_ids(self, session: AsyncSession, ids: Sequence[str]) -> list[Any]:
        from sqlalchemy import select

        if not ids:
            return []
        stmt = select(self.model).where(self.pk_column.in_(list(ids)))
        return list((await session.execute(stmt)).scalars().all())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KBSource {self.key} → {self.collection}>"
