import httpx
from app.config import settings
from app.models import Finding

SEVERITY_MAP = {
    "confirmed": "critical",
    "likely": "high",
    "insufficient": "informational",
    "false_positive": "informational",
}

# Adjust these mutation strings to match your Ghostwriter schema version
CREATE_FINDING_MUTATION = """
mutation CreateFinding($projectId: ID!, $title: String!, $description: String!,
                        $severity: String!, $cvssScore: Float, $cve: String,
                        $recommendation: String!) {
  createFinding(input: {
    project: $projectId
    title: $title
    description: $description
    severity: $severity
    cvssScore: $cvssScore
    cve: $cve
    recommendation: $recommendation
  }) {
    finding {
      id
      title
    }
  }
}
"""

GET_PROJECTS_QUERY = """
query {
  projects {
    id
    title
    client {
      name
    }
  }
}
"""

async def get_projects() -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.GHOSTWRITER_URL}/graphql",
            headers={
                "Authorization": f"Bearer {settings.GHOSTWRITER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"query": GET_PROJECTS_QUERY},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"]["projects"]

async def push_finding(finding: Finding, project_id: str) -> str:
    """Push a finding to Ghostwriter. Returns the created finding ID."""
    cve = finding.matched_cves[0] if finding.matched_cves else None
    recommendation = "\n".join(finding.recommended_next_steps) if finding.recommended_next_steps else ""

    variables = {
        "projectId": project_id,
        "title": finding.title,
        "description": finding.reasoning or finding.description,
        "severity": SEVERITY_MAP.get(finding.verdict or "", "informational"),
        "cvssScore": None,   # populate from KB entry if available
        "cve": cve,
        "recommendation": recommendation,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.GHOSTWRITER_URL}/graphql",
            headers={
                "Authorization": f"Bearer {settings.GHOSTWRITER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"query": CREATE_FINDING_MUTATION, "variables": variables},
        )
        resp.raise_for_status()
        data = resp.json()

        if "errors" in data:
            raise ValueError(f"Ghostwriter error: {data['errors']}")

        return data["data"]["createFinding"]["finding"]["id"]
