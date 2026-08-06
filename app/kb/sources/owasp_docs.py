"""Adapter for authoritative OWASP project documentation."""

from __future__ import annotations

from app.kb.base import KBSource, RetrievalHit
from app.kb.models import OwaspDocument


class OwaspDocsSource(KBSource):
    key = "owasp_docs"
    display_name = "OWASP Official Guides"
    model = OwaspDocument
    weight = 1.15

    @property
    def pk_column(self):
        return OwaspDocument.document_id

    def build_text(self, row: OwaspDocument) -> str:
        context = f"OWASP {row.project}"
        if row.version:
            context += f" {row.version}"
        return f"{context}\n{row.title}\n\n{row.body}"

    def build_payload(self, row: OwaspDocument) -> dict:
        return {
            "source": self.key,
            "doc_id": row.document_id,
            "title": row.title,
            "description": row.body[:1200],
            "project": row.project,
            "repository": row.repository,
            "version": row.version,
            "path": row.path,
            "git_sha": row.git_sha,
            "cwe_ids": row.cwe_ids or [],
            "ref_urls": row.ref_urls or [],
            "source_url": row.source_url,
        }

    def hit_from_payload(self, payload: dict, score: float) -> RetrievalHit:
        return RetrievalHit(
            source_key=self.key,
            source_label=self.display_name,
            doc_id=payload.get("doc_id") or "",
            title=payload.get("title") or payload.get("path") or "",
            text=payload.get("description") or "",
            score=score,
            url=payload.get("source_url"),
            payload={
                "project": payload.get("project"),
                "repository": payload.get("repository"),
                "version": payload.get("version"),
                "path": payload.get("path"),
                "git_sha": payload.get("git_sha"),
                "cwe_ids": payload.get("cwe_ids") or [],
                "ref_urls": payload.get("ref_urls") or [],
            },
        )
