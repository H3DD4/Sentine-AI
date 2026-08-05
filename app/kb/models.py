"""
Knowledge-base tables — ONE PHYSICAL TABLE PER SOURCE.

Design rationale
----------------
Sentinel.AI is a multi-RAG system: each knowledge source is an independent
store with its own schema, its own refresh cadence, its own Qdrant collection,
and its own failure domain.  We deliberately do NOT use a single `kb_entries`
table with a `source` discriminator column, because:

1.  Schemas genuinely differ.  An NVD CVE has CVSS sub-scores and CPE strings;
    a MITRE technique has tactics and detection guidance; a Ghostwriter finding
    has replication steps and a client engagement.  Forcing them into shared
    columns means either a sparse table full of NULLs or a lossy `extra JSON`
    blob that retrieval cannot filter on.

2.  Failure isolation.  If the Ghostwriter table or collection is unavailable,
    NVD retrieval must keep working and the UI must say so.  Separate stores
    make "this arm is down" a first-class, observable state instead of a
    partially-failed query over shared rows.

3.  Adding a source must not require a migration of existing data.  A new
    source = one new table + one new collection + one registry entry.

Each table below carries the same sync-tracking mixin so drift between
Postgres and Qdrant is detectable per source (the old single-table design had a
`qdrant_synced` flag that no code path ever wrote — see KBSyncMixin below).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    JSON,
)

from app.models import Base


# ── Shared sync-tracking mixin ────────────────────────────────────────────────


class KBSyncMixin:
    """
    Columns every KB table needs so that Postgres↔Qdrant drift is *detectable*.

    `content_hash` is the hash of the exact text that was embedded.  On re-sync
    we skip re-embedding when the hash is unchanged (cheap incremental updates),
    and we can find rows whose vectors are stale after a chunking or model
    change by comparing against the current embedding config.

    `qdrant_synced_at` is written ONLY after the vector upsert for that row has
    been confirmed.  A row with `qdrant_synced_at IS NULL` is authoritative
    evidence of drift — that is what lets the health check report a source as
    `degraded` rather than silently returning incomplete results.
    """

    content_hash = Column(String(64), nullable=True, index=True)
    embed_model = Column(String(128), nullable=True)
    qdrant_synced_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


# ── Source 1: NVD (public CVE feed) ──────────────────────────────────────────


class NVDEntry(Base, KBSyncMixin):
    """
    One row per CVE.  Natural primary key (`cve_id`) — CVE IDs are globally
    unique and stable, so a surrogate UUID would only add an indirection.
    """

    __tablename__ = "nvd_entries"

    cve_id = Column(String(32), primary_key=True)
    description = Column(Text, nullable=False, default="")
    vuln_status = Column(String(32), nullable=True)

    # Scoring
    cvss_v3 = Column(Float, nullable=True, index=True)
    cvss_v4 = Column(Float, nullable=True)
    severity = Column(String(16), nullable=True, index=True)
    exploitability_score = Column(Float, nullable=True)
    impact_score = Column(Float, nullable=True)

    # Attack profile (CVSS base vector, split out so retrieval can filter on it)
    attack_vector = Column(String(24), nullable=True)
    attack_complexity = Column(String(24), nullable=True)
    privileges_required = Column(String(24), nullable=True)
    user_interaction = Column(String(24), nullable=True)
    confidentiality_impact = Column(String(24), nullable=True)
    integrity_impact = Column(String(24), nullable=True)
    availability_impact = Column(String(24), nullable=True)

    # Classification
    cwe = Column(String(32), nullable=True, index=True)

    # Scope
    affected_products = Column(JSON, default=list, nullable=False)

    # NOTE: named `ref_urls`, NOT `references`.  `references` is a reserved word
    # in Postgres; SQLAlchemy quotes it in generated DDL but any hand-written
    # raw SQL against it silently breaks.  Renamed to remove that trap.
    ref_urls = Column(JSON, default=list, nullable=False)

    published_date = Column(DateTime, nullable=True, index=True)
    last_modified = Column(DateTime, nullable=True, index=True)

    __table_args__ = (
        Index("ix_nvd_cvss_published", "cvss_v3", "published_date"),
    )


# ── Source 2: MITRE ATT&CK (public technique catalogue) ───────────────────────


class MitreTechnique(Base, KBSyncMixin):
    """One row per ATT&CK technique or sub-technique."""

    __tablename__ = "mitre_techniques"

    technique_id = Column(String(16), primary_key=True)  # e.g. T1059.001
    name = Column(String(256), nullable=False, default="")
    description = Column(Text, nullable=False, default="")

    tactics = Column(JSON, default=list, nullable=False)       # kill-chain phases
    platforms = Column(JSON, default=list, nullable=False)     # Windows, Linux…
    data_sources = Column(JSON, default=list, nullable=False)
    detection = Column(Text, nullable=True)

    is_subtechnique = Column(Boolean, default=False, nullable=False)
    parent_technique_id = Column(String(16), nullable=True, index=True)

    ref_urls = Column(JSON, default=list, nullable=False)
    attack_version = Column(String(16), nullable=True)
    deprecated = Column(Boolean, default=False, nullable=False, index=True)


# ── Source 3: Ghostwriter (the firm's own historical findings) ────────────────


class GhostwriterFinding(Base, KBSyncMixin):
    """
    Historical findings pulled from the firm's Ghostwriter instance.

    This is the highest-value source for validation: it is how the firm has
    actually written up this class of finding before.  Kept in its own table
    because its shape (replication steps, mitigation, per-engagement linkage)
    has nothing in common with a CVE record, and because it is the one source
    whose availability depends on an internal API that can be down.
    """

    __tablename__ = "ghostwriter_findings"

    id = Column(String(64), primary_key=True)  # deterministic: "gw-<gw_id>"
    gw_id = Column(String(64), nullable=False, unique=True, index=True)

    title = Column(String(512), nullable=False, default="")
    description = Column(Text, nullable=False, default="")

    severity = Column(String(16), nullable=True, index=True)
    cvss_score = Column(Float, nullable=True)
    finding_type = Column(String(64), nullable=True, index=True)

    replication_steps = Column(Text, nullable=True)
    mitigation = Column(Text, nullable=True)
    impact = Column(Text, nullable=True)
    host_detection = Column(Text, nullable=True)
    network_detection = Column(Text, nullable=True)

    affected_entities = Column(JSON, default=list, nullable=False)
    tags = Column(JSON, default=list, nullable=False)
    mitre_techniques = Column(JSON, default=list, nullable=False)
    cve_refs = Column(JSON, default=list, nullable=False)

    # Provenance — retained for attribution and filtering in the UI, not as a
    # security boundary (this is an internal-only deployment, per product owner).
    project_id = Column(String(64), nullable=True, index=True)
    engagement_code = Column(String(64), nullable=True, index=True)
    client_name = Column(String(256), nullable=True, index=True)

    gw_created_at = Column(DateTime, nullable=True)
    gw_updated_at = Column(DateTime, nullable=True, index=True)


# ── Source 4: Internal analyst knowledge ─────────────────────────────────────


class InternalDoc(Base, KBSyncMixin):
    """
    Analyst-authored playbooks, validation checklists and tradecraft notes.
    Manually curated via the KB router.
    """

    __tablename__ = "internal_docs"

    id = Column(String(64), primary_key=True)
    title = Column(String(512), nullable=False, default="")
    body = Column(Text, nullable=False, default="")

    doc_type = Column(String(32), default="note", nullable=False, index=True)
    tags = Column(JSON, default=list, nullable=False)
    mitre_techniques = Column(JSON, default=list, nullable=False)
    validation_steps = Column(JSON, default=list, nullable=False)
    indicators = Column(JSON, default=list, nullable=False)
    ref_urls = Column(JSON, default=list, nullable=False)

    author = Column(String(256), nullable=True)


# ── Source health registry ───────────────────────────────────────────────────


class KBSourceState(Base):
    """
    Per-source operational state.  This table is what makes the two product
    requirements enforceable rather than aspirational:

    * "show in the app which data the RAG is using"  → the UI reads row_count /
      vector_count / status per source and renders it next to every answer.

    * "a rescue plan when a data source is not available"  → the retrieval
      orchestrator consults `enabled` and `status` before fanning out, skips
      arms that are down, and reports the degradation explicitly instead of
      silently returning fewer results.

    Written by the sync jobs and the health checker; read by the orchestrator
    and the /kb/sources endpoint.
    """

    __tablename__ = "kb_source_state"

    source_key = Column(String(32), primary_key=True)   # "nvd", "mitre", …
    display_name = Column(String(128), nullable=False, default="")

    enabled = Column(Boolean, default=True, nullable=False)
    status = Column(String(24), default="unknown", nullable=False)

    row_count = Column(Integer, default=0, nullable=False)
    vector_count = Column(Integer, default=0, nullable=False)
    unsynced_count = Column(Integer, default=0, nullable=False)

    last_sync_started_at = Column(DateTime, nullable=True)
    last_sync_completed_at = Column(DateTime, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
