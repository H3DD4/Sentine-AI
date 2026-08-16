"""Source adapters. Each module registers one KBSource implementation."""

from app.kb.sources.ghostwriter import GhostwriterSource
from app.kb.sources.finding_templates import FindingTemplatesSource
from app.kb.sources.internal import InternalSource
from app.kb.sources.mitre import MitreSource
from app.kb.sources.nvd import NVDSource
from app.kb.sources.owasp import OwaspSource
from app.kb.sources.owasp_docs import OwaspDocsSource

__all__ = [
    "NVDSource",
    "MitreSource",
    "OwaspSource",
    "OwaspDocsSource",
    "GhostwriterSource",
    "FindingTemplatesSource",
    "InternalSource",
]
