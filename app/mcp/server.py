"""MCP stdio server exposing deterministic security calculations.

Run with: ``python -m app.mcp.server``
"""

from mcp.server.fastmcp import FastMCP

from app.services.cvss_tool import calculate_cvss as _calculate_cvss

mcp = FastMCP("Mazars deterministic security tools")


@mcp.tool(name="calculate_cvss")
def calculate_cvss(vector: str) -> dict:
    """Validate and calculate a complete CVSS 3.1 or 4.0 vector."""
    return _calculate_cvss(vector)


if __name__ == "__main__":
    mcp.run(transport="stdio")
