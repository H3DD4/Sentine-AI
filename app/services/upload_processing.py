"""Bounded upload staging and evidence processing."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import settings
from app.services.evidence import parse_evidence_file

EVIDENCE_CONTEXT_CHARS = 32_000


async def stage_evidence_uploads(files: list[UploadFile]) -> tuple[list[dict], dict]:
    """Stream uploads to disk, then parse each bounded artifact once."""
    if len(files) > settings.EVIDENCE_MAX_FILES:
        raise HTTPException(400, f"Too many files (max {settings.EVIDENCE_MAX_FILES})")

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    total = 0
    parsed_files: list[dict] = []
    notices: list[str] = []

    for upload in files:
        safe_name = Path(upload.filename or "unknown").name or "unknown"
        staged_path = Path(settings.UPLOAD_DIR) / f".staged-{uuid.uuid4().hex}-{safe_name}"
        file_size = 0
        try:
            with staged_path.open("wb") as destination:
                while True:
                    chunk = await upload.read(settings.EVIDENCE_UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    file_size += len(chunk)
                    total += len(chunk)
                    if file_size > settings.EVIDENCE_MAX_FILE_BYTES:
                        raise HTTPException(413, f"{safe_name} exceeds the per-file upload limit")
                    if total > settings.EVIDENCE_MAX_TOTAL_BYTES:
                        raise HTTPException(413, "Total upload size exceeds the configured request limit")
                    destination.write(chunk)

            parsed = await parse_evidence_file(safe_name, staged_path, file_size)
            parsed.update(
                filename=safe_name,
                storage_path=str(staged_path),
                size_bytes=file_size,
            )
            parsed_files.append(parsed)
            notices.extend(parsed.get("processing_notices", []))
        except Exception:
            staged_path.unlink(missing_ok=True)
            cleanup_staged_evidence(parsed_files)
            raise

    text_items = [item for item in parsed_files if item.get("extracted_text")]
    per_file = EVIDENCE_CONTEXT_CHARS // len(text_items) if text_items else 0
    for item in text_items:
        item["analysis_text"] = _representative_excerpt(item["extracted_text"], per_file)
        item["selected_chars"] = len(item["analysis_text"])
        if len(item["analysis_text"]) < len(item["extracted_text"]):
            item["processing_notices"].append(
                f"{len(item['analysis_text']):,} of {item['extracted_chars']:,} extracted characters "
                "were selected for this analysis; the complete artifact was retained."
            )
    notices = [
        notice
        for item in parsed_files
        for notice in item.get("processing_notices", [])
    ]

    manifest = {
        "files": [
            {
                "filename": item["filename"],
                "file_type": item["file_type"],
                "size_bytes": item["size_bytes"],
                "extracted_chars": item.get("extracted_chars", 0),
                "selected_chars": item.get("selected_chars", 0),
                "needs_manual_review": item.get("needs_manual_review", False),
                "notices": item.get("processing_notices", []),
            }
            for item in parsed_files
        ],
        "total_bytes": total,
        "notices": list(dict.fromkeys(notices)),
    }
    return parsed_files, manifest


def _representative_excerpt(text: str, limit: int) -> str:
    if not limit or len(text) <= limit:
        return text
    head = limit * 2 // 3
    return text[:head] + "\n\n[... middle omitted from analysis ...]\n\n" + text[-(limit - head):]


def move_staged_evidence(parsed_files: list[dict], finding_id: str) -> None:
    """Put staged artifacts under stable finding-specific names."""
    for index, item in enumerate(parsed_files):
        source = Path(item["storage_path"])
        destination = Path(settings.UPLOAD_DIR) / f"{finding_id}-{index}-{item['safe_filename']}"
        source.replace(destination)
        item["storage_path"] = str(destination)


def cleanup_staged_evidence(parsed_files: list[dict]) -> None:
    for item in parsed_files:
        path = item.get("storage_path")
        if path:
            Path(path).unlink(missing_ok=True)
