"""
Evidence parsing service — fully async.
Handles text extraction from PDFs, images (via vision model), and plain text.
"""

import io
from pathlib import Path
import fitz  # PyMuPDF
from PIL import Image
from app.services.validation import describe_image

SUPPORTED_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
SUPPORTED_TEXT_TYPES = {".txt", ".log", ".csv", ".xml", ".json", ".yaml", ".yml", ".md"}
SUPPORTED_PDF_TYPES = {".pdf"}
SUPPORTED_OFFICE_TYPES = {".docx", ".doc"}

# Security: limit file sizes
MAX_TEXT_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB
MAX_PDF_BYTES = 50 * 1024 * 1024    # 50 MB


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
        if len(file_bytes) > MAX_TEXT_BYTES:
            file_bytes = file_bytes[:MAX_TEXT_BYTES]
        text = file_bytes.decode("utf-8", errors="replace")
        return {
            "file_type": "text",
            "extracted_text": text[:50_000],  # cap at 50k chars for LLM context
            "image_description": None,
            "needs_manual_review": False,
            "safe_filename": safe_name,
        }

    if ext in SUPPORTED_IMAGE_TYPES:
        if len(file_bytes) > MAX_IMAGE_BYTES:
            return {
                "file_type": "image",
                "extracted_text": None,
                "image_description": "(Image too large — exceeds 20 MB limit)",
                "needs_manual_review": True,
                "safe_filename": safe_name,
            }
        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        }
        media_type = mime_map.get(ext, "image/png")
        description = await describe_image(file_bytes, media_type)
        return {
            "file_type": "image",
            "extracted_text": None,
            "image_description": description,
            "needs_manual_review": False,
            "safe_filename": safe_name,
        }

    if ext in SUPPORTED_PDF_TYPES:
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
            }
        return {
            "file_type": "pdf",
            "extracted_text": text[:50_000],
            "image_description": None,
            "needs_manual_review": False,
            "safe_filename": safe_name,
        }

    # Unsupported types (PCAPs, binaries, etc.)
    return {
        "file_type": "binary",
        "extracted_text": None,
        "image_description": None,
        "needs_manual_review": True,
        "safe_filename": safe_name,
    }
