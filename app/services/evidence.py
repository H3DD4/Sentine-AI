"""
Evidence parsing service — fully async.
Handles text extraction from PDFs, images (via vision model), and plain text.
"""

import io
import logging
from pathlib import Path
import fitz  # PyMuPDF
from PIL import Image
from app.services.validation import describe_image

log = logging.getLogger(__name__)

SUPPORTED_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
SUPPORTED_TEXT_TYPES = {".txt", ".log", ".csv", ".xml", ".json", ".yaml", ".yml", ".md"}
SUPPORTED_PDF_TYPES = {".pdf"}
SUPPORTED_OFFICE_TYPES = {".docx", ".doc"}

# Security: limit file sizes
MAX_TEXT_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB
MAX_PDF_BYTES = 50 * 1024 * 1024    # 50 MB


async def parse_evidence_file(filename: str, path: Path, size_bytes: int) -> dict:
    """Read only the bytes required by the parser from a staged artifact."""
    ext = Path(filename).suffix.lower()
    if ext in SUPPORTED_TEXT_TYPES:
        read_limit = min(size_bytes, MAX_TEXT_BYTES)
    elif ext in SUPPORTED_IMAGE_TYPES:
        if size_bytes > MAX_IMAGE_BYTES:
            return {
                "file_type": "image",
                "extracted_text": None,
                "image_description": "(Image too large - exceeds vision processing limit)",
                "needs_manual_review": True,
                "safe_filename": Path(filename).name,
                "extracted_chars": 0,
                "selected_chars": 0,
                "processing_notices": [
                    "The image was retained but requires manual review because it exceeds the vision limit."
                ],
            }
        read_limit = size_bytes
    elif ext in SUPPORTED_PDF_TYPES:
        read_limit = min(size_bytes, MAX_PDF_BYTES)
    else:
        read_limit = 0

    with path.open("rb") as source:
        if ext in SUPPORTED_TEXT_TYPES and size_bytes > read_limit:
            head = read_limit * 2 // 3
            tail = read_limit - head
            content = source.read(head)
            source.seek(-tail, 2)
            content += (
                b"\n\n[... middle omitted from extraction; complete artifact retained ...]\n\n"
                + source.read(tail)
            )
        else:
            content = source.read(read_limit)
    parsed = await parse_evidence(filename, content)
    if size_bytes > read_limit and ext in SUPPORTED_TEXT_TYPES:
        parsed["processing_notices"].insert(
            0, f"Text extraction capped at {read_limit:,} bytes; the complete file was retained."
        )
    elif size_bytes > read_limit and ext in SUPPORTED_PDF_TYPES:
        parsed["processing_notices"].insert(
            0, f"PDF parsing capped at {read_limit:,} bytes; the complete file was retained."
        )
    return parsed


async def parse_evidence(filename: str, file_bytes: bytes) -> dict:
    """
    Parse uploaded evidence file and extract text or image description.

    Returns:
        {
            "file_type": str,
            "extracted_text": str | None,
            "image_description": str | None,
            "needs_manual_review": bool,
        }
    """
    # Sanitize: strip any path components from the filename (path traversal prevention)
    safe_name = Path(filename).name
    ext = Path(safe_name).suffix.lower()

    if ext in SUPPORTED_TEXT_TYPES:
        original_bytes = len(file_bytes)
        if len(file_bytes) > MAX_TEXT_BYTES:
            file_bytes = file_bytes[:MAX_TEXT_BYTES]
        text = file_bytes.decode("utf-8", errors="replace")
        selected = _representative_excerpt(text, 50_000)
        notices = []
        if original_bytes > MAX_TEXT_BYTES:
            notices.append(
                f"Text extraction capped at {MAX_TEXT_BYTES:,} bytes; the complete file was retained."
            )
        if len(selected) < len(text):
            notices.append(
                f"{len(selected):,} of {len(text):,} extracted characters were selected for analysis."
            )
        return {
            "file_type": "text",
            "extracted_text": selected,
            "image_description": None,
            "needs_manual_review": False,
            "safe_filename": safe_name,
            "extracted_chars": len(text),
            "selected_chars": len(selected),
            "processing_notices": notices,
        }

    if ext in SUPPORTED_IMAGE_TYPES:
        if len(file_bytes) > MAX_IMAGE_BYTES:
            return {
                "file_type": "image",
                "extracted_text": None,
                "image_description": "(Image too large — exceeds 20 MB limit)",
                "needs_manual_review": True,
                "safe_filename": safe_name,
                "extracted_chars": 0,
                "selected_chars": 0,
                "processing_notices": ["The image was retained but requires manual review because it exceeds the vision limit."],
            }
        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        }
        media_type = mime_map.get(ext, "image/png")
        try:
            description = await describe_image(file_bytes, media_type)
        except Exception as exc:
            log.warning("Vision extraction failed for %s: %s", safe_name, exc)
            return {
                "file_type": "image",
                "extracted_text": None,
                "image_description": "(Automatic image analysis unavailable; manual review required)",
                "needs_manual_review": True,
                "safe_filename": safe_name,
                "extracted_chars": 0,
                "selected_chars": 0,
                "processing_notices": [
                    "The image was retained but automatic analysis failed; manual review is required."
                ],
            }
        return {
            "file_type": "image",
            "extracted_text": None,
            "image_description": description,
            "needs_manual_review": False,
            "safe_filename": safe_name,
            "extracted_chars": len(description),
            "selected_chars": len(description),
            "processing_notices": [],
        }

    if ext in SUPPORTED_PDF_TYPES:
        original_bytes = len(file_bytes)
        if len(file_bytes) > MAX_PDF_BYTES:
            file_bytes = file_bytes[:MAX_PDF_BYTES]
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            text = "\n".join(page.get_text() for page in doc)
        except Exception as exc:
            return {
                "file_type": "pdf",
                "extracted_text": f"(PDF parse error: {exc})",
                "image_description": None,
                "needs_manual_review": True,
                "safe_filename": safe_name,
                "extracted_chars": 0,
                "selected_chars": 0,
                "processing_notices": ["The PDF was retained but automatic extraction failed; manual review is required."],
            }
        selected = _representative_excerpt(text, 50_000)
        notices = []
        if original_bytes > MAX_PDF_BYTES:
            notices.append(
                f"PDF parsing capped at {MAX_PDF_BYTES:,} bytes; the complete file was retained."
            )
        if len(selected) < len(text):
            notices.append(
                f"{len(selected):,} of {len(text):,} extracted characters were selected for analysis."
            )
        return {
            "file_type": "pdf",
            "extracted_text": selected,
            "image_description": None,
            "needs_manual_review": False,
            "safe_filename": safe_name,
            "extracted_chars": len(text),
            "selected_chars": len(selected),
            "processing_notices": notices,
        }

    # Unsupported types (PCAPs, binaries, etc.)
    return {
        "file_type": "binary",
        "extracted_text": None,
        "image_description": None,
        "needs_manual_review": True,
        "safe_filename": safe_name,
        "extracted_chars": 0,
        "selected_chars": 0,
        "processing_notices": [
            "This file type was retained but could not be extracted automatically; manual review is required."
        ],
    }


def _representative_excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return (
        text[:head]
        + "\n\n[... middle omitted from analysis; complete artifact retained ...]\n\n"
        + text[-tail:]
    )
