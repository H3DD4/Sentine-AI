"""Exercise model-output safety with three adversarial completions.

This uses the production grounding validator, not a simplified parser. It
proves that model-proposed identifiers, titles, CVSS scores, and unsupported
claims cannot become authoritative output without deterministic evidence.
"""

from __future__ import annotations

import json

from app.kb.base import RetrievalHit, SearchOutcome
from app.services.chat_grounding import GroundedChatDraft, validate_draft


def outcome() -> SearchOutcome:
    return SearchOutcome(hits=[
        RetrievalHit(
            source_key="nvd", source_label="NVD CVE Feed", doc_id="CVE-2023-27159",
            title="CVE-2023-27159", text="Severity: HIGH CVSS 7.5",
            score=1.0, payload={"cvss_v3": 7.5, "severity": "HIGH"},
        ),
        RetrievalHit(
            source_key="mitre", source_label="MITRE ATT&CK", doc_id="T1552.005",
            title="T1552.005 - Cloud Instance Metadata API",
            text="Cloud metadata APIs can expose credentials.", score=0.8,
            payload={"mitre_techniques": ["T1552.005"]},
        ),
    ])


def draft(answer: str, identifier: str, score: float) -> GroundedChatDraft:
    return GroundedChatDraft.model_validate({
        "answer_markdown": answer,
        "claims": [{
            "level": "observed", "statement": "The issue exposes credentials.",
            "evidence_basis": "The retrieved record", "evidence_ids": ["KB1"],
            "conditions": [],
        }],
        "mappings": [{
            "mapping_type": "cve", "identifier": identifier, "name": "Invented title",
            "applicability": "direct", "rationale": "Model claim", "evidence_basis": "KB1",
            "source": "nvd", "source_doc_id": identifier,
        }],
        "cvss": {"status": "exact", "version": "3.1", "vector": "", "rationale": "model", "score": score},
    })


def main() -> None:
    tests = [
        draft("CVE-2023-27159 is critical [KB1].", "CVE-2023-27159", 9.9),
        draft("The technique is T1552.005.", "CVE-2099-0001", 10.0),
        draft("Credentials were exfiltrated [KB2].", "T1552.005", 8.8),
    ]
    results = []
    for index, candidate in enumerate(tests, 1):
        checked = validate_draft(candidate, outcome(), user_text="Assess the evidence")
        rendered = checked.draft.model_dump(mode="json")
        passed = (
            bool(checked.issues)
            and rendered["cvss"]["status"] == "pending_evidence"
            and rendered["cvss"].get("score") is None
        )
        results.append({"attempt": index, "passed": passed, "issues": checked.issues, "result": rendered})
    print(json.dumps({"passed": all(item["passed"] for item in results), "attempts": results}, indent=2, default=str))
    if not all(item["passed"] for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
