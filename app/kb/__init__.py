"""
Multi-source knowledge base.

Each source is an independent store: its own Postgres table, its own Qdrant
collection, its own sync schedule, its own failure domain.  See `models.py` for
why we chose physical separation over a single table with a source column.
"""

from app.kb.base import (
    Availability,
    KBSource,
    RetrievalHit,
    SearchOutcome,
    SourceReport,
)
from app.kb.registry import (
    all_sources,
    get_health,
    get_source,
    invalidate_health_cache,
    resolve_sources,
    source_keys,
)

__all__ = [
    "Availability",
    "KBSource",
    "RetrievalHit",
    "SearchOutcome",
    "SourceReport",
    "all_sources",
    "get_health",
    "get_source",
    "invalidate_health_cache",
    "resolve_sources",
    "source_keys",
]
