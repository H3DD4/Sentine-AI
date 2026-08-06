import unittest

from app.ingestion.mitre_sync import _external_id, _release_version
from app.kb.models import MitreTechnique
from app.kb.sources.mitre import MitreSource


class MitreSyncTests(unittest.TestCase):
    def test_extracts_attack_release_from_redirected_asset_url(self):
        self.assertEqual(
            _release_version(
                "https://github.com/mitre-attack/attack-stix-data/releases/download/v19.2/enterprise-attack.json"
            ),
            "19.2",
        )
        self.assertIsNone(_release_version("https://example.test/enterprise-attack.json"))

    def test_extracts_only_mitre_attack_external_id(self):
        obj = {
            "external_references": [
                {"source_name": "other", "external_id": "X1"},
                {"source_name": "mitre-attack", "external_id": "T1059.001"},
            ]
        }

        self.assertEqual(_external_id(obj), "T1059.001")

    def test_source_matches_parent_and_subtechnique_ids(self):
        source = MitreSource()

        self.assertEqual(source.extract_ids("T1059 and T1059.001"), ["T1059", "T1059.001"])

    def test_indexed_text_carries_release_provenance(self):
        row = MitreTechnique(
            technique_id="T1190",
            name="Exploit Public-Facing Application",
            description="Adversaries may exploit an internet-facing system.",
            attack_version="19.2",
        )

        self.assertIn("ATT&CK version: 19.2", MitreSource().build_text(row))


if __name__ == "__main__":
    unittest.main()
