"""Async client for the local deterministic MCP tool server."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client



_PROJECT_ROOT = Path(__file__).resolve().parents[2]


async def calculate_cvss_via_mcp(vector: str) -> dict:
    """Call the MCP server's calculate_cvss tool and decode its JSON result."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        cwd=_PROJECT_ROOT,
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool("calculate_cvss", {"vector": vector})
            for content in result.content:
                text = getattr(content, "text", None)
                if text:
                    return json.loads(text)
    raise RuntimeError("The CVSS MCP tool returned no result")


def extract_cvss_tool_request(value: str) -> dict | None:
    """Extract the model's JSON-shaped tool request without trusting its arguments."""
    text = str(value or "").strip()
    candidates = [text]
    if "{" in text and "}" in text:
        candidates.append(text[text.find("{"):text.rfind("}") + 1])
    for candidate in candidates:
        try:
            request = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(request, dict) or request.get("tool") != "calculate_cvss":
            continue
        arguments = request.get("arguments")
        return arguments if isinstance(arguments, dict) else {}
    return None


def render_cvss_tool_result(calculation: dict) -> str:
    """Render only authoritative values returned by the MCP calculator."""
    if calculation.get("status") != "valid":
        return "**Technical severity**\n- Pending evidence. The proposed CVSS vector was invalid."
    return (
        "**Technical severity**\n"
        f"- CVSS {calculation['version']}: **{calculation['score']:.1f} "
        f"{calculation['severity'].title()}**\n"
        f"- Vector: `{calculation['canonical_vector']}`\n"
        "- Basis: Calculated by the deterministic CVSS MCP tool from the model candidate."
    )


async def validate_draft_cvss_via_mcp(draft, *, explicit_vector_present: bool) -> list[str]:
    """Validate model candidates through MCP; explicit analyst vectors use Python directly."""
    if explicit_vector_present:
        return []

    vectors = []
    if draft.cvss.status == "exact" and draft.cvss.vector:
        vectors.append(draft.cvss.vector)
    elif draft.cvss.status == "range":
        for scenario in (draft.cvss.lower_bound, draft.cvss.upper_bound):
            if scenario and scenario.vector:
                vectors.append(scenario.vector)

    issues = []
    for vector in vectors:
        try:
            result = await calculate_cvss_via_mcp(vector)
        except Exception as exc:
            issues.append(f"CVSS MCP calculation failed ({type(exc).__name__}).")
            continue
        if result.get("status") != "valid":
            issues.append("A model-proposed CVSS vector was rejected by the CVSS MCP tool.")
    return issues
