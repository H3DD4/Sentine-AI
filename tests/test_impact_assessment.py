import unittest

from pydantic import ValidationError

from app.schemas import (
    BusinessPriority,
    CVSSAssessment,
    CVSSScenario,
    ClarificationQuestion,
    ImpactAssessment,
    ImpactClaim,
    ImpactEvidenceLevel,
    ValidationResult,
)
from app.kb.base import RetrievalHit, SearchOutcome
from app.services.validation import _normalize_assessment, impact_narrative


class ImpactAssessmentTests(unittest.TestCase):
    def test_legacy_validation_output_gets_conservative_impact_default(self):
        result = ValidationResult(
            verdict="confirmed",
            confidence=0.9,
            reasoning="The response proves the issue.",
            matched_cves=[],
            matched_techniques=[],
            missing_evidence=[],
            recommended_next_steps=[],
        )

        self.assertEqual(
            result.impact_assessment.business_priority,
            BusinessPriority.pending_context,
        )
        self.assertFalse(result.impact_assessment.context_complete)

    def test_clarification_batch_is_limited_to_three_questions(self):
        questions = [
            ClarificationQuestion(
                question=f"Question {index}",
                why_it_matters="It changes priority.",
            )
            for index in range(4)
        ]

        with self.assertRaises(ValidationError):
            ImpactAssessment(clarification_questions=questions)

    def test_questions_force_pending_business_priority(self):
        assessment = ImpactAssessment(
            business_priority=BusinessPriority.critical,
            context_complete=True,
            clarification_questions=[
                ClarificationQuestion(
                    question="Is this the production payment service?",
                    why_it_matters="It determines process criticality.",
                )
            ],
        )

        self.assertEqual(assessment.business_priority, BusinessPriority.pending_context)
        self.assertFalse(assessment.context_complete)

    def test_cvss_range_cannot_publish_an_exact_score(self):
        assessment = CVSSAssessment(
            status="range",
            version="3.1",
            vector="CVSS:3.1/SHOULD/BE/CLEARED",
            score=9.9,
            severity="critical",
            lower_bound=CVSSScenario(
                label="Established",
                vector="CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N",
                score=6.5,
                severity="medium",
            ),
            upper_bound=CVSSScenario(
                label="Conditional write access",
                vector="CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:H",
                score=9.1,
                severity="critical",
                assumptions=["The role has destructive permissions."],
            ),
            unresolved_metrics=["Integrity and availability depend on effective IAM permissions."],
        )

        self.assertIsNone(assessment.score)
        self.assertEqual(assessment.vector, "")
        self.assertEqual(assessment.severity, "")

    def test_unretrieved_mapping_is_downgraded_and_not_matched(self):
        data = {
            "matched_cves": ["CVE-2021-44228"],
            "matched_techniques": ["T9999"],
            "mappings": [
                {
                    "mapping_type": "attack",
                    "identifier": "T9999",
                    "name": "Invented",
                    "applicability": "direct",
                    "rationale": "The model guessed it.",
                    "evidence_basis": "None",
                    "source": "mitre",
                    "source_doc_id": "T9999",
                }
            ],
        }

        _normalize_assessment(data, SearchOutcome())

        self.assertEqual(data["mappings"][0]["applicability"], "unsupported")
        self.assertEqual(data["matched_cves"], [])
        self.assertEqual(data["matched_techniques"], [])

    def test_exact_retrieved_direct_mapping_reaches_legacy_list(self):
        data = {
            "matched_cves": [],
            "matched_techniques": [],
            "mappings": [
                {
                    "mapping_type": "attack",
                    "identifier": "T1552.005",
                    "name": "Cloud Instance Metadata API",
                    "applicability": "direct",
                    "rationale": "IMDS credential access was observed.",
                    "evidence_basis": "Metadata credential response",
                    "source": "mitre",
                    "source_doc_id": "T1552.005",
                }
            ],
        }
        outcome = SearchOutcome(hits=[
            RetrievalHit(
                source_key="mitre",
                source_label="MITRE ATT&CK",
                doc_id="T1552.005",
                title="Cloud Instance Metadata API",
                text="Adversaries may access cloud instance metadata APIs.",
                score=1.0,
            )
        ])

        _normalize_assessment(data, outcome)

        self.assertEqual(data["mappings"][0]["applicability"], "direct")
        self.assertEqual(data["matched_techniques"], ["T1552.005"])

    def test_cvss_score_and_severity_are_calculated_not_trusted(self):
        data = {
            "matched_cves": [],
            "matched_techniques": [],
            "mappings": [],
            "impact_assessment": {
                "cvss": {
                    "status": "exact",
                    "version": "3.1",
                    "vector": "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N",
                    "score": 0.1,
                    "severity": "low",
                }
            },
        }

        _normalize_assessment(data, SearchOutcome())

        self.assertEqual(data["impact_assessment"]["cvss"]["score"], 6.5)
        self.assertEqual(data["impact_assessment"]["cvss"]["severity"], "medium")

    def test_observed_claim_without_retrieved_evidence_is_downgraded(self):
        data = {
            "matched_cves": [],
            "matched_techniques": [],
            "mappings": [],
            "impact_assessment": {
                "claims": [
                    {
                        "level": "observed",
                        "statement": "The target exposed credentials.",
                        "evidence_basis": "The model believes this happened.",
                        "evidence_ids": ["FAKE99"],
                    }
                ]
            },
        }

        _normalize_assessment(data, SearchOutcome(), text_evidence_count=1)

        claim = data["impact_assessment"]["claims"][0]
        self.assertEqual(claim["level"], "conditional")
        self.assertEqual(claim["evidence_ids"], [])

    def test_observed_claim_citing_only_kb_precedent_is_downgraded(self):
        data = {
            "matched_cves": [],
            "matched_techniques": [],
            "mappings": [],
            "impact_assessment": {
                "claims": [
                    {
                        "level": "observed",
                        "statement": "The target leaks IAM credentials.",
                        "evidence_basis": "A prior finding describes it.",
                        "evidence_ids": ["KB1"],
                    }
                ]
            },
        }
        outcome = SearchOutcome(hits=[
            RetrievalHit(
                source_key="ghostwriter",
                source_label="Ghostwriter",
                doc_id="prior-ssrf",
                title="Prior SSRF finding",
                text="A previous target leaked credentials.",
                score=1.0,
            )
        ])

        _normalize_assessment(data, outcome)

        self.assertEqual(data["impact_assessment"]["claims"][0]["level"], "conditional")
        self.assertTrue(data["grounding_issues"])

    def test_logically_demonstrated_claim_citing_only_kb_is_downgraded(self):
        data = {
            "matched_cves": [],
            "matched_techniques": [],
            "mappings": [],
            "impact_assessment": {
                "claims": [{
                    "level": "logically_demonstrated",
                    "statement": "The target role can read production data.",
                    "evidence_basis": "A prior finding had broad permissions.",
                    "evidence_ids": ["KB1"],
                }]
            },
        }
        outcome = SearchOutcome(hits=[
            RetrievalHit(
                source_key="ghostwriter",
                source_label="Ghostwriter",
                doc_id="prior-role",
                title="Prior cloud finding",
                text="A different target had broad role permissions.",
                score=1.0,
            )
        ])

        _normalize_assessment(data, outcome)

        self.assertEqual(data["impact_assessment"]["claims"][0]["level"], "conditional")
        self.assertTrue(data["grounding_issues"])

    def test_cross_source_cve_mapping_is_rejected(self):
        data = {
            "matched_cves": [],
            "matched_techniques": [],
            "mappings": [
                {
                    "mapping_type": "cve",
                    "identifier": "CVE-2024-12345",
                    "applicability": "direct",
                    "rationale": "Model attached a CVE to an OWASP document.",
                    "source": "owasp_docs",
                    "source_doc_id": "CVE-2024-12345",
                }
            ],
        }
        outcome = SearchOutcome(hits=[
            RetrievalHit(
                source_key="owasp_docs",
                source_label="OWASP Official Guides",
                doc_id="CVE-2024-12345",
                title="Unrelated guide",
                text="Guide text.",
                score=1.0,
            )
        ])

        _normalize_assessment(data, outcome)

        self.assertEqual(data["mappings"][0]["applicability"], "unsupported")
        self.assertEqual(data["matched_cves"], [])

    def test_template_code_requires_matching_template_payload(self):
        data = {
            "matched_cves": [],
            "matched_techniques": [],
            "mappings": [
                {
                    "mapping_type": "template",
                    "identifier": "ASIA_V_013",
                    "applicability": "supporting",
                    "rationale": "Approved template language.",
                    "source": "finding_templates",
                    "source_doc_id": "row-hash-1",
                }
            ],
        }
        outcome = SearchOutcome(hits=[
            RetrievalHit(
                source_key="finding_templates",
                source_label="Internal finding templates",
                doc_id="row-hash-1",
                title="Template",
                text="Template text.",
                score=1.0,
                payload={"template_code": "ASIA_V_013"},
            )
        ])

        _normalize_assessment(data, outcome)

        self.assertEqual(data["mappings"][0]["applicability"], "supporting")

    def test_malformed_cvss_becomes_pending_evidence(self):
        data = {
            "matched_cves": [],
            "matched_techniques": [],
            "mappings": [],
            "impact_assessment": {
                "cvss": {
                    "status": "exact",
                    "version": "3.1",
                    "vector": "CVSS:3.1/AV:INVALID",
                    "score": 9.9,
                    "severity": "critical",
                }
            },
        }

        _normalize_assessment(data, SearchOutcome())

        self.assertEqual(data["impact_assessment"]["cvss"]["status"], "pending_evidence")

    def test_mixed_cvss_range_versions_become_pending_evidence(self):
        data = {
            "matched_cves": [],
            "matched_techniques": [],
            "mappings": [],
            "impact_assessment": {
                "cvss": {
                    "status": "range",
                    "lower_bound": {
                        "vector": "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:L/I:N/A:N",
                        "score": 1.0,
                    },
                    "upper_bound": {
                        "vector": "CVSS:4.0/AV:N/AC:L/AT:N/PR:L/UI:N/VC:H/VI:N/VA:N/SC:N/SI:N/SA:N",
                        "score": 9.0,
                    },
                }
            },
        }

        _normalize_assessment(data, SearchOutcome())

        self.assertEqual(data["impact_assessment"]["cvss"]["status"], "pending_evidence")

    def test_contradictory_mapping_decisions_are_both_rejected(self):
        base = {
            "mapping_type": "attack",
            "identifier": "T1552.005",
            "source": "mitre",
            "source_doc_id": "T1552.005",
            "rationale": "Decision",
        }
        data = {
            "matched_cves": [],
            "matched_techniques": [],
            "mappings": [
                {**base, "applicability": "direct"},
                {**base, "applicability": "rejected"},
            ],
        }
        outcome = SearchOutcome(hits=[
            RetrievalHit(
                source_key="mitre",
                source_label="MITRE ATT&CK",
                doc_id="T1552.005",
                title="Cloud Instance Metadata API",
                text="Technique text.",
                score=1.0,
            )
        ])

        _normalize_assessment(data, outcome)

        self.assertTrue(all(m["applicability"] == "unsupported" for m in data["mappings"]))
        self.assertEqual(data["matched_techniques"], [])

    def test_report_narrative_separates_conditional_and_excluded_impact(self):
        result = ValidationResult(
            verdict="confirmed",
            confidence=0.95,
            reasoning="Credentials were returned.",
            matched_cves=[],
            matched_techniques=[],
            missing_evidence=[],
            recommended_next_steps=[],
            impact_assessment=ImpactAssessment(
                demonstrated_capability="A low-privilege user retrieved cloud credentials.",
                technical_impact="Credential confidentiality was lost.",
                business_impact="Target-specific business impact is pending role permissions.",
                business_priority=BusinessPriority.pending_context,
                priority_rationale="The credential scope is unknown.",
                claims=[
                    ImpactClaim(
                        level=ImpactEvidenceLevel.conditional,
                        statement="Production data may be exposed if the role can read it.",
                        evidence_basis="A complete credential set was returned.",
                        conditions=["The role has production data permissions."],
                    )
                ],
                excluded_claims=["AWS account takeover was not established."],
            ),
        )

        narrative = impact_narrative(result)

        self.assertIn("Demonstrated capability", narrative)
        self.assertIn("Conditional impact", narrative)
        self.assertIn("Not established", narrative)
        self.assertIn("Pending Context", narrative)


if __name__ == "__main__":
    unittest.main()
