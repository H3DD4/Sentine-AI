"""Sync authoritative English content from major official OWASP projects."""

from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from dataclasses import dataclass

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.owasp_sync import _clean_text
from app.kb.indexer import delete_document, index_rows
from app.kb.models import OwaspDocument
from app.kb.registry import get_source
from app.services.retrieval import get_qdrant

log = logging.getLogger(__name__)

_CWE = re.compile(r"\bCWE-\d+\b", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s)>\]]+")


@dataclass(frozen=True)
class ProjectSpec:
    key: str
    repository: str
    branch: str
    version: str
    path_pattern: re.Pattern[str]


PROJECTS = (
    ProjectSpec(
        "cheat-sheets",
        "OWASP/CheatSheetSeries",
        "master",
        "latest",
        re.compile(r"^cheatsheets/[^/]+\.md$", re.IGNORECASE),
    ),
    ProjectSpec(
        "wstg",
        "OWASP/wstg",
        "master",
        "latest",
        re.compile(r"^document/.+\.md$", re.IGNORECASE),
    ),
    ProjectSpec(
        "asvs",
        "OWASP/ASVS",
        "master",
        "5.0",
        re.compile(r"^5\.0/en/[^/]+\.md$", re.IGNORECASE),
    ),
    ProjectSpec(
        "api-security",
        "OWASP/API-Security",
        "master",
        "2023",
        re.compile(r"^editions/2023/en/[^/]+\.md$", re.IGNORECASE),
    ),
)


async def sync_owasp_documents(session: AsyncSession) -> dict:
    source = get_source("owasp_docs")
    qdrant = get_qdrant()
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        parsed_docs: list[dict] = []
        project_counts: dict[str, int] = {}
        for spec in PROJECTS:
            response = await client.get(
                f"https://codeload.github.com/{spec.repository}/zip/refs/heads/{spec.branch}"
            )
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                files = []
                for archived_path in archive.namelist():
                    path = archived_path.split("/", 1)[-1]
                    if not spec.path_pattern.match(path):
                        continue
                    content = archive.read(archived_path)
                    git_sha = hashlib.sha1(
                        f"blob {len(content)}\0".encode("ascii") + content
                    ).hexdigest()
                    files.append(
                        _parse_document(
                            spec,
                            path,
                            git_sha,
                            content.decode("utf-8-sig", errors="replace"),
                        )
                    )
                files = [document for document in files if _is_substantive(document["body"])]
            if not files:
                raise RuntimeError(f"No authoritative documents found for {spec.repository}")
            project_counts[spec.key] = len(files)
            parsed_docs.extend(files)

    rows: list[OwaspDocument] = []
    for parsed in parsed_docs:
        row = await session.get(OwaspDocument, parsed["document_id"])
        if row is None:
            row = OwaspDocument(**parsed)
            session.add(row)
        else:
            for key, value in parsed.items():
                setattr(row, key, value)
        rows.append(row)

    await session.commit()
    log.info("OWASP official guides: %d documents persisted, indexing", len(rows))
    stats = await index_rows(source, session, qdrant, rows, batch_size=32)
    if stats.failed:
        raise RuntimeError(
            f"OWASP guide indexing failed for {stats.failed} documents; stale data retained"
        )

    canonical_ids = [row.document_id for row in rows]
    stale_ids = list(
        (
            await session.execute(
                select(OwaspDocument.document_id).where(
                    OwaspDocument.document_id.not_in(canonical_ids)
                )
            )
        ).scalars()
    )
    for document_id in stale_ids:
        await delete_document(qdrant, source, document_id)
    if stale_ids:
        await session.execute(
            delete(OwaspDocument).where(OwaspDocument.document_id.in_(stale_ids))
        )
        await session.commit()

    return {
        "projects": project_counts,
        "rows": len(rows),
        "removed_stale_rows": len(stale_ids),
        **stats.to_dict(),
    }


def _parse_document(spec: ProjectSpec, path: str, git_sha: str, markdown: str) -> dict:
    document_id = hashlib.sha256(f"{spec.key}:{path}".encode("utf-8")).hexdigest()[:32]
    title = next(
        (
            line.lstrip("# ").strip()
            for line in markdown.splitlines()
            if line.startswith("# ")
        ),
        path.rsplit("/", 1)[-1].removesuffix(".md").replace("_", " ").replace("-", " "),
    )
    body = _clean_text(markdown)
    return {
        "document_id": document_id,
        "project": spec.key,
        "repository": spec.repository,
        "branch": spec.branch,
        "version": spec.version,
        "path": path,
        "title": _clean_text(title),
        "body": body,
        "git_sha": git_sha,
        "source_url": f"https://github.com/{spec.repository}/blob/{spec.branch}/{path}",
        "cwe_ids": sorted({value.upper() for value in _CWE.findall(markdown)}),
        "ref_urls": list(
            dict.fromkeys(url.rstrip(".,") for url in _URL.findall(markdown))
        )[:100],
    }


def _is_substantive(body: str) -> bool:
    normalized = " ".join(body.lower().split())
    return len(normalized) >= 100 and "this content has been removed" not in normalized
