"""Deterministic CVSS calculation shared by the application and MCP server."""

from __future__ import annotations

from cvss import CVSS3, CVSS4
from cvss.exceptions import CVSS3MalformedError, CVSS4MalformedError


def calculate_cvss(vector: str) -> dict:
    """Validate, canonicalize, and calculate one complete CVSS vector.

    This function deliberately does not infer metrics or repair malformed input.
    """
    raw = str(vector or "").strip()
    try:
        calculator = CVSS4(raw) if raw.upper().startswith("CVSS:4.0/") else CVSS3(raw)
        canonical = calculator.clean_vector()
        return {
            "status": "valid",
            "version": "4.0" if canonical.startswith("CVSS:4.0/") else "3.1",
            "input_vector": raw,
            "canonical_vector": canonical,
            "score": float(calculator.scores()[0]),
            "severity": calculator.severities()[0].lower(),
        }
    except (CVSS3MalformedError, CVSS4MalformedError, KeyError, TypeError, ValueError) as exc:
        return {
            "status": "invalid",
            "version": "",
            "input_vector": raw,
            "canonical_vector": "",
            "score": None,
            "severity": "",
            "error": "The vector is not a complete valid CVSS 3.1 or 4.0 vector.",
            "error_type": type(exc).__name__,
        }


CVSS_TOOL_SCHEMA = {
    "name": "calculate_cvss",
    "description": (
        "Validate and calculate a complete CVSS 3.1 or 4.0 vector. "
        "Do not infer or repair metrics; submit a complete candidate vector."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"vector": {"type": "string", "description": "Complete CVSS vector"}},
        "required": ["vector"],
        "additionalProperties": False,
    },
}
