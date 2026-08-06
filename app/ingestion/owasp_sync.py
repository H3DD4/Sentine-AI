"""OWASP Top 10 sync from the official project repository."""

from __future__ import annotations

import logging
import re

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.kb.indexer import delete_document, index_rows
from app.kb.models import OwaspTop10Entry
from app.kb.registry import get_source
from app.services.retrieval import get_qdrant

log = logging.getLogger(__name__)

_CATEGORY_FILE = re.compile(r"^A(0[1-9]|10)_(20\d{2})-(.+)\.md$")
_CWE = re.compile(r"\bCWE-\d+\b", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s)>\]]+")
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)(?:\{[^}]*\})?")


async def sync_owasp_top10(session: AsyncSession) -> dict:
    source = get_source("owasp")
    qdrant = get_qdrant()

    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        releases = await _releases_since(
            client, settings.OWASP_TOP10_CONTENTS_URL, first_year=2021
        )
        rows: list[OwaspTop10Entry] = []
        for contents_url, year in releases:
            response = await _github_get(client, contents_url)
            files = sorted(
                (
                    item
                    for item in response.json()
                    if (match := _CATEGORY_FILE.match(item.get("name", "")))
                    and int(match.group(2)) == year
                ),
                key=lambda item: item["name"],
            )
            if len(files) != 10:
                raise RuntimeError(
                    f"Expected 10 canonical OWASP {year} categories, discovered {len(files)}"
                )

            for item in files:
                document = await client.get(item["download_url"])
                document.raise_for_status()
                parsed = _parse_category(item["name"], document.text, item["html_url"])
                row = await session.get(OwaspTop10Entry, parsed["category_id"])
                if row is None:
                    row = OwaspTop10Entry(**parsed)
                    session.add(row)
                else:
                    for key, value in parsed.items():
                        setattr(row, key, value)
                rows.append(row)

    await session.commit()
    log.info("OWASP Top 10: %d categories persisted, indexing", len(rows))
    stats = await index_rows(source, session, qdrant, rows)
    if stats.failed:
        raise RuntimeError(
            f"OWASP indexing failed for {stats.failed} categories; existing data retained"
        )

    release_years = [year for _, year in releases]
    canonical_ids = [row.category_id for row in rows]
    stale_ids = list(
        (
            await session.execute(
                select(OwaspTop10Entry.category_id).where(
                    OwaspTop10Entry.year >= 2021,
                    OwaspTop10Entry.category_id.not_in(canonical_ids),
                )
            )
        ).scalars()
    )
    for category_id in stale_ids:
        await delete_document(qdrant, source, category_id)
    if stale_ids:
        await session.execute(
            delete(OwaspTop10Entry).where(OwaspTop10Entry.category_id.in_(stale_ids))
        )
        await session.commit()

    log.info("OWASP Top 10 sync complete: %s", stats)
    return {
        "releases": release_years,
        "rows": len(rows),
        "removed_stale_rows": len(stale_ids),
        **stats.to_dict(),
    }


async def _github_get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    response = await client.get(url, headers={"Accept": "application/vnd.github+json"})
    response.raise_for_status()
    return response


async def _releases_since(
    client: httpx.AsyncClient, configured_url: str, *, first_year: int
) -> list[tuple[str, int]]:
    """Resolve every complete official English release from ``first_year`` onward."""
    configured_url = configured_url.rstrip("/")
    direct_match = re.search(r"/(20\d{2})/docs/en$", configured_url)
    if direct_match:
        year = int(direct_match.group(1))
        if year < first_year:
            raise RuntimeError(f"Configured OWASP release {year} predates {first_year}")
        return [(configured_url, year)]

    root = await _github_get(client, configured_url)
    years = sorted(
        (
            int(item["name"])
            for item in root.json()
            if item.get("type") == "dir" and re.fullmatch(r"20\d{2}", item.get("name", ""))
            and int(item["name"]) >= first_year
        ),
    )
    releases: list[tuple[str, int]] = []
    for year in years:
        url = f"{configured_url}/{year}/docs/en"
        try:
            response = await _github_get(client, url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                continue
            raise
        category_count = sum(
            1
            for item in response.json()
            if (match := _CATEGORY_FILE.match(item.get("name", "")))
            and int(match.group(2)) == year
        )
        if category_count == 10:
            releases.append((url, year))

    if not releases:
        raise RuntimeError(
            f"No complete OWASP Top 10 release from {first_year} onward found in the official repository"
        )
    return releases


def _parse_category(filename: str, markdown: str, source_url: str) -> dict:
    match = _CATEGORY_FILE.match(filename)
    if not match:
        raise ValueError(f"Not an OWASP Top 10 category file: {filename}")

    rank = int(match.group(1))
    year = int(match.group(2))
    category_id = f"A{rank:02d}:{year}"
    sections = _sections(markdown)
    heading = next(
        (line.lstrip("# ").strip() for line in markdown.splitlines() if line.startswith("# ")),
        f"{category_id} - {match.group(3).replace('_', ' ')}",
    )
    heading = _clean_text(heading)
    name = re.sub(rf"^{re.escape(category_id)}\s*[–—-]?\s*", "", heading).strip()
    full_text = _clean_text(markdown)

    return {
        "category_id": category_id,
        "rank": rank,
        "year": year,
        "name": name,
        "overview": sections.get("overview", ""),
        "description": sections.get("description", ""),
        "prevention": sections.get("how to prevent", ""),
        "scenarios": sections.get("example attack scenarios", ""),
        "full_text": full_text,
        "cwe_ids": sorted({value.upper() for value in _CWE.findall(markdown)}),
        "ref_urls": list(dict.fromkeys(url.rstrip(".,") for url in _URL.findall(markdown)))[:50],
        "source_url": source_url,
    }


def _sections(markdown: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in markdown.splitlines():
        if line.startswith("## "):
            current = line[3:].strip().rstrip(".").lower()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {key: _clean_text("\n".join(lines)) for key, lines in sections.items()}


def _clean_text(markdown: str) -> str:
    text = _IMAGE.sub("", markdown)
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\{:[^}]+\}", "", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"[*_`]", "", text)
    text = re.sub(r"^\s*\|.*\|\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
