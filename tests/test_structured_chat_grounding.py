import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from app.kb.base import Availability, RetrievalHit, SearchOutcome, SourceReport
from app.services.chat_grounding import (
    GroundedChatDraft,
    generate_structured_response,
    generate_conversational_response,
    parse_draft,
    render_grounded_response,
    validate_draft,
)


def _outcome() -> SearchOutcome:
    return SearchOutcome(hits=[
        RetrievalHit(
            source_key="mitre",
            source_label="MITRE ATT&CK",
            doc_id="T1552.005",
            title="Cloud Instance Metadata API",
            text="Adversaries may access cloud metadata APIs to collect credentials.",
            score=1.0,
            payload={"technique_id": "T1552.005"},
        ),
        RetrievalHit(
            source_key="finding_templates",
            source_label="Internal finding templates",
            doc_id="row-13",
            title="Canonical internal title",
            text="Approved example language only.",
            score=0.8,
            payload={"template_code": "ASIA_V_013"},
        ),
    ])


def _draft(**overrides) -> GroundedChatDraft:
    data = {
        "answer_markdown": "Credential retrieval was observed [KB1].",
        "claims": [{
            "level": "observed",
            "statement": "Temporary credentials were returned.",
            "evidence_basis": "The supplied response contained credentials.",
            "evidence_ids": ["FINDING1"],
            "conditions": [],
        }],
        "mappings": [{
            "mapping_type": "attack",
            "identifier": "T1552.005",
            "name": "Model-authored wrong title",
            "applicability": "direct",
            "rationale": "Metadata credentials were returned.",
            "evidence_basis": "FINDING1",
            "source": "mitre",
            "source_doc_id": "T1552.005",
        }],
        "cvss": {
            "status": "pending_evidence",
            "rationale": "Role permissions and scope remain unknown.",
        },
        "limitations": [],
        "assumptions": [],
    }
    data.update(overrides)
    return GroundedChatDraft.model_validate(data)


class StructuredChatGroundingTests(unittest.TestCase):
    def test_python_renders_canonical_title_not_model_title(self):
        result = validate_draft(_draft(), _outcome(), user_text="Finding evidence")

        rendered = render_grounded_response(result, _outcome())

        self.assertIn("T1552.005 - Cloud Instance Metadata API", rendered)
        self.assertNotIn("Model-authored wrong title", rendered)
        self.assertIn("[mitre:T1552.005]", rendered)

    def test_source_spoofed_mapping_is_rejected(self):
        draft = _draft(mappings=[{
            "mapping_type": "cve",
            "identifier": "CVE-2024-12345",
            "applicability": "direct",
            "rationale": "Invented",
            "source": "mitre",
            "source_doc_id": "T1552.005",
        }])

        result = validate_draft(draft, _outcome(), user_text="Finding evidence")

        self.assertEqual(result.draft.mappings[0].applicability, "unsupported")
        self.assertTrue(result.issues)

    def test_template_alias_is_validated_against_payload(self):
        draft = _draft(mappings=[{
            "mapping_type": "template",
            "identifier": "ASIA_V_013",
            "applicability": "supporting",
            "rationale": "Approved wording precedent.",
            "source": "finding_templates",
            "source_doc_id": "row-13",
        }])

        result = validate_draft(draft, _outcome(), user_text="Finding evidence")

        self.assertEqual(result.draft.mappings[0].applicability, "supporting")
        self.assertFalse(result.issues)

    def test_nonexistent_kb_reference_triggers_revision(self):
        result = validate_draft(
            _draft(answer_markdown="This is supported by [KB99]."),
            _outcome(),
            user_text="Finding evidence",
        )

        self.assertTrue(any("KB99" in issue for issue in result.issues))

    def test_authoritative_identifier_in_narrative_triggers_revision(self):
        result = validate_draft(
            _draft(answer_markdown="The applicable technique is T1552.005."),
            _outcome(),
            user_text="Finding evidence",
        )

        self.assertTrue(any("Python must own" in issue for issue in result.issues))

    def test_exact_model_vector_without_analyst_vector_becomes_pending(self):
        draft = _draft(cvss={
            "status": "exact",
            "version": "3.1",
            "vector": "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:N",
            "rationale": "Model proposal",
        })

        result = validate_draft(draft, _outcome(), user_text="No vector was supplied.")

        self.assertEqual(result.draft.cvss.status, "pending_evidence")
        self.assertIsNone(result.draft.cvss.score)

    def test_model_proposed_range_without_current_user_vectors_becomes_pending(self):
        lower = "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N"
        upper = "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:N"
        draft = _draft(cvss={
            "status": "range", "version": "3.1", "rationale": "Model range",
            "lower_bound": {"label": "lower", "vector": lower, "assumptions": [], "rationale": "Disclosure"},
            "upper_bound": {"label": "upper", "vector": upper, "assumptions": ["Writes are possible"], "rationale": "Modification"},
            "unresolved_metrics": ["Integrity permissions"],
        })

        result = validate_draft(draft, _outcome(), user_text="No vector supplied")

        self.assertEqual(result.draft.cvss.status, "pending_evidence")
        self.assertIsNone(result.draft.cvss.lower_bound)

    def test_current_turn_range_renders_bound_assumptions(self):
        lower = "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:N/A:N"
        upper = "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:N"
        draft = _draft(cvss={
            "status": "range", "version": "3.1", "rationale": "Analyst scenarios",
            "lower_bound": {"label": "lower", "vector": lower, "assumptions": ["Authentication is required"], "rationale": "Disclosure"},
            "upper_bound": {"label": "upper", "vector": upper, "assumptions": ["Administrative writes are possible"], "rationale": "Modification"},
            "unresolved_metrics": ["Integrity permissions"],
        })

        result = validate_draft(draft, _outcome(), user_text=f"Use {lower} and {upper}")
        response = render_grounded_response(result, _outcome())

        self.assertEqual(result.draft.cvss.status, "range")
        self.assertIn("Authentication is required", response)
        self.assertIn("Administrative writes are possible", response)

    def test_exact_analyst_vector_is_calculated_by_python(self):
        vector = "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:N"
        draft = _draft(cvss={
            "status": "exact",
            "version": "3.1",
            "vector": vector,
            "rationale": "Analyst supplied vector",
        })

        result = validate_draft(draft, _outcome(), user_text=f"Use {vector}")

        self.assertEqual(result.draft.cvss.status, "exact")
        self.assertEqual(result.draft.cvss.score, 9.6)
        self.assertEqual(result.draft.cvss.severity, "critical")

    def test_kb_only_logical_claim_is_downgraded(self):
        draft = _draft(claims=[{
            "level": "logically_demonstrated",
            "statement": "The role can delete production data.",
            "evidence_basis": "A KB record discusses cloud credentials.",
            "evidence_ids": ["KB1"],
            "conditions": [],
        }])

        result = validate_draft(draft, _outcome(), user_text="Finding evidence")

        claim = result.draft.claims[0]
        self.assertEqual(claim.level.value, "conditional")
        self.assertTrue(claim.conditions)

    def test_requested_unretrieved_cve_gets_deterministic_status(self):
        outcome = _outcome()
        outcome.reports.append(SourceReport(
            source_key="nvd",
            display_name="NVD CVE Feed",
            availability=Availability.NO_MATCH,
        ))
        result = validate_draft(
            _draft(answer_markdown="No applicable CVE was identified."),
            outcome,
            user_text="Verify CVE-2020-1938 but do not use it if unrelated.",
        )

        response = render_grounded_response(result, outcome)

        self.assertIn("CVE-2020-1938", response)
        self.assertIn("no exact authoritative record was retrieved this turn", response)

    def test_requested_identifier_reports_source_failure_not_absence(self):
        outcome = _outcome()
        outcome.reports.append(SourceReport(
            source_key="nvd",
            display_name="NVD CVE Feed",
            availability=Availability.UNAVAILABLE,
            detail="connection refused",
        ))
        result = validate_draft(
            _draft(), outcome, user_text="Verify CVE-2020-1938."
        )

        response = render_grounded_response(result, outcome)

        self.assertIn("not verified", response)
        self.assertIn("unavailable", response)

    def test_malformed_draft_fails_closed(self):
        draft, issues = parse_draft({"claims": [{"level": "observed"}]})

        self.assertTrue(issues)
        self.assertTrue(draft.limitations)

    def test_clean_draft_uses_one_model_call(self):
        client = AsyncMock()
        client.generate_validation = AsyncMock(return_value=_draft().model_dump(mode="json"))

        response, result = asyncio.run(generate_structured_response(
            client,
            [{"role": "user", "content": "Finding evidence"}],
            _outcome(),
        ))

        self.assertEqual(client.generate_validation.await_count, 1)
        self.assertFalse(result.corrected)
        self.assertIn("Cloud Instance Metadata API", response)
        self.assertEqual(
            client.generate_validation.await_args.kwargs["parse_attempts"], 1
        )

    def test_selected_model_is_used_for_draft_and_correction(self):
        unsafe = _draft(answer_markdown="T9999 proves impact.").model_dump(mode="json")
        client = AsyncMock()
        client.generate_validation = AsyncMock(side_effect=[unsafe, unsafe])

        asyncio.run(generate_structured_response(
            client,
            [{"role": "user", "content": "Finding evidence"}],
            _outcome(),
            model="selected/model",
        ))

        self.assertEqual(client.generate_validation.await_count, 2)
        self.assertEqual(
            [call.kwargs["model"] for call in client.generate_validation.await_args_list],
            ["selected/model", "selected/model"],
        )

    def test_conversational_response_uses_plain_text_generation(self):
        client = AsyncMock()
        client.generate = AsyncMock(return_value="A useful Markdown answer.")
        client.generate_validation = AsyncMock()

        response, issues = asyncio.run(generate_conversational_response(
            client,
            [{"role": "user", "content": "Explain the finding"}],
            _outcome(),
            model="selected/model",
        ))

        self.assertEqual(response, "A useful Markdown answer.")
        self.assertEqual(issues, [])
        client.generate_validation.assert_not_awaited()
        self.assertEqual(client.generate.await_args.kwargs["model"], "selected/model")
        self.assertIn("Do not return JSON", client.generate.await_args.kwargs["system"])

    def test_conversational_response_does_not_parse_json_like_text(self):
        client = AsyncMock()
        client.generate = AsyncMock(return_value='{not valid JSON, but still useful prose')
        client.generate_validation = AsyncMock()

        response, _ = asyncio.run(generate_conversational_response(
            client,
            [{"role": "user", "content": "Explain the finding"}],
            _outcome(),
        ))

        self.assertEqual(response, '{not valid JSON, but still useful prose')
        client.generate_validation.assert_not_awaited()

    def test_unsafe_draft_gets_one_correction_then_python_strips_remaining_value(self):
        unsafe = _draft(answer_markdown="T9999 proves impact.").model_dump(mode="json")
        client = AsyncMock()
        client.generate_validation = AsyncMock(side_effect=[unsafe, unsafe])

        response, result = asyncio.run(generate_structured_response(
            client,
            [{"role": "user", "content": "Finding evidence"}],
            _outcome(),
        ))

        self.assertEqual(client.generate_validation.await_count, 2)
        self.assertTrue(result.corrected)
        self.assertNotIn("T9999", response)
        self.assertNotIn("authoritative value", response)
        self.assertIn("Technical severity", response)

    def test_renderer_drops_placeholder_and_inline_wrong_cvss_sentences(self):
        draft = _draft(answer_markdown=(
            "# Constat JWT\n"
            "La confusion d'algorithme a permis un accès non autorisé. "
            "La base locale ne contient pas [authoritative value rendered below] (jsonwebtoken).\n"
            "Score de base : 9.1 (Critique).\n"
            "Vecteur : [authoritative value rendered below].\n"
            "MITRE ATT&CK : [authoritative value rendered below] — Application Access Token.\n"
            "Les hashes bcrypt observés ne sont pas des mots de passe en clair."
        ))
        result = validate_draft(draft, _outcome(), user_text="Finding evidence")

        response = render_grounded_response(result, _outcome())

        self.assertIn("# Constat JWT", response)
        self.assertIn("accès non autorisé", response)
        self.assertIn("ne sont pas des mots de passe en clair", response)
        self.assertNotIn("authoritative value", response)
        self.assertNotIn("9.1", response)
        self.assertNotIn("Score de base", response)
        self.assertNotIn("Application Access Token", response)
        self.assertIn("Pending evidence", response)

    def test_inline_score_without_vector_triggers_revision(self):
        result = validate_draft(
            _draft(answer_markdown="Score de base : 9.1 (Critique)."),
            _outcome(),
            user_text="Finding evidence",
        )

        self.assertTrue(any("CVSS score" in issue for issue in result.issues))

    def test_inline_severity_without_score_triggers_revision(self):
        result = validate_draft(
            _draft(answer_markdown="Sévérité : Critique."),
            _outcome(),
            user_text="Finding evidence",
        )

        self.assertTrue(any("severity label" in issue for issue in result.issues))

    def test_owasp_identifier_in_narrative_is_python_owned(self):
        result = validate_draft(
            _draft(answer_markdown="La catégorie A01:2025 s'applique."),
            _outcome(),
            user_text="Finding evidence",
        )

        self.assertTrue(any("Python must own" in issue for issue in result.issues))

    def test_itemized_cvss_metrics_trigger_revision_and_are_removed(self):
        draft = _draft(answer_markdown=(
            "Attack Vector (AV:N) : Exploitable à distance.\n"
            "Privileges Required (PR:N) : Aucun privilège.\n"
            "Confidentiality (C:H) : Emails et hachages bcrypt.\n"
            "Integrity (I:H) : Modification possible.\n"
            "Availability (A:N) : Aucun impact démontré."
        ))

        result = validate_draft(draft, _outcome(), user_text="Finding evidence")
        response = render_grounded_response(result, _outcome())

        self.assertTrue(any("itemized CVSS" in issue for issue in result.issues))
        self.assertNotIn("AV:N", response)
        self.assertNotIn("PR:N", response)
        self.assertNotIn("C:H", response)
        self.assertNotIn("I:H", response)
        self.assertNotIn("A:N", response)

    def test_isolated_metric_and_ordinary_low_count_are_preserved(self):
        draft = _draft(answer_markdown=(
            "Le protocole observé mentionne AV:N dans un champ de test. "
            "3 comptes à privilège faible ont été testés."
        ))

        result = validate_draft(draft, _outcome(), user_text="Finding evidence")
        response = render_grounded_response(result, _outcome())

        self.assertFalse(any("itemized CVSS" in issue for issue in result.issues))
        self.assertIn("AV:N", response)
        self.assertIn("3 comptes à privilège faible", response)

    def test_cwe_status_accepts_owasp_docs_authority(self):
        outcome = _outcome()
        outcome.reports.append(SourceReport(
            source_key="owasp_docs",
            display_name="OWASP Official Guides",
            availability=Availability.OK,
            hits=1,
        ))
        outcome.hits.append(RetrievalHit(
            source_key="owasp_docs",
            source_label="OWASP Official Guides",
            doc_id="owasp-cwe-284",
            title="Authorization guidance",
            text="CWE-284 guidance.",
            score=0.9,
            payload={"cwe_ids": ["CWE-284"]},
        ))
        result = validate_draft(
            _draft(answer_markdown="Vérification de CWE-284 demandée."),
            outcome,
            user_text="Verify CWE-284.",
        )

        response = render_grounded_response(result, outcome)

        self.assertIn("retrieved as [owasp_docs:owasp-cwe-284]", response)

    def test_valid_kb_reference_is_preserved_inside_mapping_rationale(self):
        draft = _draft(mappings=[{
            "mapping_type": "attack",
            "identifier": "T1552.005",
            "applicability": "direct",
            "rationale": "This is corroborated by [KB1].",
            "evidence_basis": "FINDING1",
            "source": "mitre",
            "source_doc_id": "T1552.005",
        }])

        result = validate_draft(draft, _outcome(), user_text="Finding evidence")
        response = render_grounded_response(result, _outcome())

        self.assertIn("corroborated by [mitre:T1552.005]", response)

    def test_forged_canonical_citation_is_removed(self):
        result = validate_draft(
            _draft(answer_markdown="Claim supported by [owasp_docs:fake-record]."),
            _outcome(),
            user_text="Finding evidence",
        )
        response = render_grounded_response(result, _outcome())

        self.assertNotIn("owasp_docs:fake-record", response)

    def test_retrieved_title_is_escaped_before_rendering(self):
        outcome = _outcome()
        outcome.hits[0].title = "<script>alert(1)</script>\nInjected heading"
        result = validate_draft(_draft(), outcome, user_text="Finding evidence")

        response = render_grounded_response(result, outcome)

        self.assertNotIn("<script>", response)
        self.assertIn("&lt;script&gt;", response)
        self.assertNotIn("\nInjected heading", response)

    def test_exact_vector_matching_is_case_and_order_insensitive(self):
        vector = "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:N"
        reordered_lower = "cvss:3.1/ui:n/pr:l/ac:l/av:n/s:c/a:n/i:h/c:h"
        draft = _draft(cvss={
            "status": "exact",
            "version": "3.1",
            "vector": vector,
            "rationale": "Analyst supplied vector",
        })

        result = validate_draft(draft, _outcome(), user_text=reordered_lower)

        self.assertEqual(result.draft.cvss.status, "exact")
        self.assertEqual(result.draft.cvss.score, 9.6)

    def test_prior_turn_vector_does_not_authorize_current_exact_score(self):
        vector = "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:N"
        client = AsyncMock()
        client.generate_validation = AsyncMock(return_value=_draft(cvss={
            "status": "exact", "version": "3.1", "vector": vector,
            "rationale": "Quoted prior vector",
        }).model_dump(mode="json"))

        _, result = asyncio.run(generate_structured_response(
            client,
            [
                {"role": "user", "content": f"Earlier vector {vector}"},
                {"role": "assistant", "content": "Previous answer"},
                {"role": "user", "content": "Why was that selected?"},
            ],
            _outcome(),
        ))

        self.assertEqual(result.draft.cvss.status, "pending_evidence")

    def test_model_score_fields_do_not_enter_proposed_type(self):
        data = _draft().model_dump(mode="json")
        data["cvss"] = {
            "status": "exact", "version": "3.1",
            "vector": "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:N",
            "score": 1.2, "severity": "low", "rationale": "Model values",
        }

        draft, issues = parse_draft(data)

        self.assertFalse(issues)
        self.assertFalse(hasattr(draft.cvss, "score"))

    def test_itemized_metrics_are_local_to_their_paragraph(self):
        draft = _draft(answer_markdown=(
            "AV:N and PR:N describe the proposed CVSS metrics.\n\n"
            "The endpoint returned an HTTP response with AV:N in a diagnostic field."
        ))
        result = validate_draft(draft, _outcome(), user_text="Finding evidence")
        response = render_grounded_response(result, _outcome())

        self.assertNotIn("AV:N and PR:N", response)
        self.assertIn("AV:N in a diagnostic field", response)

    def test_worse_correction_does_not_replace_useful_first_draft(self):
        first = _draft(
            answer_markdown="Useful finding analysis. T9999 must not be rendered."
        ).model_dump(mode="json")
        worse = _draft(answer_markdown="").model_dump(mode="json")
        client = AsyncMock()
        client.generate_validation = AsyncMock(side_effect=[first, worse])

        response, result = asyncio.run(generate_structured_response(
            client,
            [{"role": "user", "content": "Finding evidence"}],
            _outcome(),
        ))

        self.assertTrue(result.corrected)
        self.assertIn("Cloud Instance Metadata API", response)
        self.assertIn("original draft retained", " ".join(result.issues))

    def test_correction_transport_failure_preserves_first_draft(self):
        client = AsyncMock()
        client.generate_validation = AsyncMock(side_effect=[
            _draft(answer_markdown="The technique is T1552.005.").model_dump(mode="json"),
            TimeoutError("provider timeout"),
        ])

        response, result = asyncio.run(generate_structured_response(
            client,
            [{"role": "user", "content": "Finding evidence"}],
            _outcome(),
        ))

        self.assertIn("Cloud Instance Metadata API", response)
        self.assertIn("correction", " ".join(result.issues).lower())

    def test_malformed_first_completion_uses_correction_instead_of_failing(self):
        client = AsyncMock()
        client.generate_validation = AsyncMock(side_effect=[
            json.JSONDecodeError("invalid", "not-json", 0),
            _draft().model_dump(mode="json"),
        ])

        response, result = asyncio.run(generate_structured_response(
            client,
            [{"role": "user", "content": "Finding evidence"}],
            _outcome(),
        ))

        self.assertEqual(client.generate_validation.await_count, 2)
        self.assertTrue(result.corrected)
        self.assertIn("Cloud Instance Metadata API", response)

    def test_two_malformed_completions_return_safe_fallback(self):
        client = AsyncMock()
        client.generate_validation = AsyncMock(side_effect=[
            json.JSONDecodeError("invalid", "first", 0),
            json.JSONDecodeError("invalid", "second", 0),
        ])

        response, result = asyncio.run(generate_structured_response(
            client,
            [{"role": "user", "content": "Verify CVE-2022-23529"}],
            _outcome(),
        ))

        self.assertEqual(client.generate_validation.await_count, 2)
        self.assertTrue(result.corrected)
        self.assertIn("CVE-2022-23529", response)
        self.assertIn("safe fallback", " ".join(result.issues))
        self.assertIn("Knowledge sources used", response)

    def test_pipeline_stage_order_is_observable(self):
        client = AsyncMock()
        client.generate_validation = AsyncMock(return_value=_draft().model_dump(mode="json"))
        stages = []

        async def stage(key, label, status, detail):
            stages.append((key, status))

        asyncio.run(generate_structured_response(
            client,
            [{"role": "user", "content": "Finding evidence"}],
            _outcome(),
            stage=stage,
        ))

        self.assertEqual(stages, [
            ("draft", "active"),
            ("draft", "complete"),
            ("validate", "active"),
            ("validate", "complete"),
            ("finalize", "active"),
            ("finalize", "complete"),
        ])


if __name__ == "__main__":
    unittest.main()
