import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from app.ingestion.finding_templates_sync import parse_docx
from app.ingestion.finding_templates_text import clean_for_embedding
from app.kb.sources.finding_templates import FindingTemplatesSource


SOURCE_DIR = Path(__file__).resolve().parents[1] / "app" / "kb" / "sources"


class FindingTemplateParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = sorted(SOURCE_DIR.glob("*.docx"))
        if len(files) != 2:
            raise unittest.SkipTest("the two redacted finding-template DOCX files are required")
        cls.by_file = {path.name: parse_docx(path) for path in files}
        cls.records = [record for records in cls.by_file.values() for record in records]

    def test_extracts_every_structured_record_table(self):
        counts = sorted(len(records) for records in self.by_file.values())
        self.assertEqual(counts, [66, 248])
        self.assertEqual(len(self.records), 314)
        self.assertEqual(Counter(r.record_kind for r in self.records), {
            "positive_practice": 32,
            "vulnerability": 282,
        })

    def test_duplicate_visible_codes_have_distinct_stable_ids(self):
        duplicates = [record for record in self.records if record.template_code == "V_004"]
        self.assertGreater(len(duplicates), 2)
        self.assertEqual(len({record.id for record in duplicates}), len(duplicates))
        reparsed = {
            record.id
            for path in sorted(SOURCE_DIR.glob("*.docx"))
            for record in parse_docx(path)
        }
        self.assertEqual(reparsed, {record.id for record in self.records})

    def test_heading_context_tracks_document_order(self):
        xss = next(
            record
            for record in self.records
            if record.template_code == "V_019" and "Scripting" in record.title
        )
        self.assertIn("XSS", xss.category.upper())
        self.assertTrue(xss.section)

    def test_conditional_waf_risks_are_lossless(self):
        conditional = [record for record in self.records if len(record.risk_assessments) == 2]
        self.assertEqual({record.template_code for record in conditional}, {"V_013", "V_019"})
        for record in conditional:
            self.assertEqual(
                {risk["condition"] for risk in record.risk_assessments},
                {"with_waf", "without_waf"},
            )
            for risk in record.risk_assessments:
                self.assertTrue({"impact_level", "likelihood", "criticality", "finding_type"} <= risk.keys())

    def test_ordinary_risk_rows_parse_both_label_value_pairs(self):
        ordinary = next(
            record
            for record in self.records
            if record.record_kind == "vulnerability" and len(record.risk_assessments) == 1
        )
        self.assertTrue(
            {"impact_level", "likelihood", "criticality", "finding_type"}
            <= ordinary.risk_assessments[0].keys()
        )

    def test_embedding_cleanup_removes_images_but_keeps_entity_types(self):
        cleaned = clean_for_embedding("Preuve [image] sur [IP] pour [client ] via [url]")
        self.assertNotIn("[image]", cleaned)
        self.assertIn("IP address", cleaned)
        self.assertIn("the organisation", cleaned)
        self.assertIn("URL", cleaned)


class FindingTemplateSourceTests(unittest.TestCase):
    def test_adapter_builds_labelled_text_and_payload(self):
        row = SimpleNamespace(
            id="stable-id",
            template_code="ASIA_V_013",
            record_kind="vulnerability",
            source_file="templates.docx",
            source_table_index=10,
            title="Application [client] vulnerable",
            category="Session",
            topic="Session handling",
            iso_references=["A.9.4.1"],
            observations="Observed on [IP] [image]",
            evidence_template="[image]",
            affected_elements="[client] portal",
            impact="Account access",
            recommendation="Regenerate tokens",
            implementation_complexity="Medium",
            implementation_priority="Short term",
            risk_assessments=[{
                "condition": "default",
                "impact_level": "High",
                "likelihood": "Likely",
                "criticality": "High",
                "finding_type": "Technical",
            }],
        )
        source = FindingTemplatesSource()
        text = source.build_text(row)
        payload = source.build_payload(row)
        self.assertIn("Template ID: ASIA_V_013", text)
        self.assertIn("Observation: Observed on IP address", text)
        self.assertNotIn("[image]", text)
        self.assertEqual(payload["doc_id"], "stable-id")
        self.assertEqual(payload["template_code"], "ASIA_V_013")
        self.assertEqual(source.exact_id_payload_field, "template_code")


if __name__ == "__main__":
    unittest.main()
