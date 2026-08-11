import httpx
from urllib.parse import urlsplit

from app.config import settings
from app.models import Finding

CREATE_FINDING_MUTATION = """
mutation CreateFinding($object: reportedFinding_insert_input!) {
  insert_reportedFinding_one(object: $object) {
    id
    title
  }
}
"""

GET_PROJECTS_QUERY = """
query Projects {
  project {
    id
    codename
    client {
      name
    }
    reports(where: {archived: {_eq: false}}) {
      id
      title
    }
  }
}
"""

GET_SEVERITY_QUERY = """
query Severity($name: String!) {
  findingSeverity(where: {severity: {_ilike: $name}}, limit: 1) {
    id
  }
}
"""

GET_FINDINGS_QUERY = """
query Findings {
  finding {
    id
    title
    description
    cvssScore
    replication_steps
    mitigation
    impact
    hostDetectionTechniques
    networkDetectionTechniques
    references
    severity {
      severity
    }
    type {
      findingType
    }
  }
}
"""


def _graphql_url() -> str:
    base = settings.GHOSTWRITER_URL.strip().rstrip("/")
    if not base:
        raise ValueError("GHOSTWRITER_URL is not configured")
    if base.endswith("/v1/graphql") or base.endswith("/graphql"):
        return base
    return f"{base}/v1/graphql"


def _headers() -> dict[str, str]:
    token = settings.GHOSTWRITER_API_KEY.strip()
    if not token:
        raise ValueError("GHOSTWRITER_API_KEY is not configured")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _verify_tls() -> bool:
    hostname = urlsplit(_graphql_url()).hostname
    return settings.GHOSTWRITER_VERIFY_TLS and hostname not in {"localhost", "127.0.0.1"}


async def _execute(query: str, variables: dict | None = None) -> dict:
    async with httpx.AsyncClient(
        timeout=30,
        verify=_verify_tls(),
        follow_redirects=True,
    ) as client:
        response = await client.post(
            _graphql_url(),
            headers=_headers(),
            json={"query": query, "variables": variables or {}},
        )
        response.raise_for_status()
        data = response.json()
    if data.get("errors"):
        message = "; ".join(
            str(error.get("message", error)) for error in data["errors"]
        )
        if "not found in type: 'query_root'" in message:
            raise ValueError(
                "Ghostwriter accepted the HTTP request but exposed only its public GraphQL "
                "schema. This API key is not granting an authenticated GraphQL role."
            )
        raise ValueError(f"Ghostwriter GraphQL error: {message}")
    if not isinstance(data.get("data"), dict):
        raise ValueError("Ghostwriter returned no GraphQL data")
    return data["data"]


async def get_projects() -> list[dict]:
    data = await _execute(GET_PROJECTS_QUERY)
    return [
        {
            "id": str(report["id"]),
            "title": " / ".join(filter(None, [project.get("codename"), report.get("title")]))
            or f"Report {report['id']}",
            "client": project.get("client") or {"name": ""},
        }
        for project in data.get("project", [])
        for report in project.get("reports", [])
    ]


async def get_findings() -> list[dict]:
    data = await _execute(GET_FINDINGS_QUERY)
    return data.get("finding", [])

async def push_finding(finding: Finding, project_id: str) -> str:
    """Create a reported finding in the selected Ghostwriter report."""
    severity = (finding.severity or "informational").strip()
    severity_data = await _execute(GET_SEVERITY_QUERY, {"name": severity})
    severities = severity_data.get("findingSeverity", [])
    if not severities:
        raise ValueError(f"Ghostwriter has no finding severity named {severity!r}")

    references = "\n".join(finding.matched_cves or [])
    variables = {"object": {
        "reportId": int(project_id),
        "title": finding.title,
        "description": finding.description or finding.reasoning or "",
        "impact": finding.impact or "",
        "mitigation": "\n".join(finding.recommended_next_steps or []),
        "replication_steps": "\n".join(finding.reproduction_steps or []),
        "affectedEntities": finding.affected_scope or "",
        "references": references,
        "severityId": severities[0]["id"],
        "cvssScore": finding.cvss_score,
        "cvssVector": finding.cvss_vector or "",
        "complete": bool(finding.analyst_confirmed),
    }}

    data = await _execute(CREATE_FINDING_MUTATION, variables)
    created = data.get("insert_reportedFinding_one")
    if not created:
        raise ValueError("Ghostwriter did not create the reported finding")
    return str(created["id"])
