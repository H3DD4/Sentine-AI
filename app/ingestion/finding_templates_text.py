"""Pure text normalization helpers for finding-template ingestion and indexing."""

from __future__ import annotations

import re


PLACEHOLDER_RE = re.compile(r"\[\s*([^\]]+?)\s*\]")


def clean_for_embedding(value: str) -> str:
    """Remove redacted image tokens while retaining useful entity types."""
    value = (value or "").replace("\xa0", " ")

    def replace(match: re.Match[str]) -> str:
        token = re.sub(r"\s+", " ", match.group(1)).strip().lower()
        replacements = {
            "client": "the organisation",
            "my_enterprise": "the security team",
            "name": "the system",
            "names": "the systems",
            "xx": "the application",
            "yy": "the application",
            "app": "the application",
            "app1": "application 1",
            "app2": "application 2",
            "app3": "application 3",
            "ip": "IP address",
            "ips": "IP addresses",
            "url": "URL",
            "path": "path",
            "user": "user",
            "users": "users",
            "domain": "domain",
            "date": "date",
        }
        if token == "image":
            return ""
        if token in replacements:
            return replacements[token]
        if token.isdigit():
            return "the application"
        return ""

    value = PLACEHOLDER_RE.sub(replace, value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n+ *", "\n", value)
    return value.strip()
