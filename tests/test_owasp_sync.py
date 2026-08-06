import unittest

from app.ingestion.owasp_sync import _parse_category
from app.kb.sources.owasp import OwaspSource


class OwaspSyncTests(unittest.TestCase):
    def test_parses_current_release_year_and_sections(self):
        parsed = _parse_category(
            "A05_2025-Injection.md",
            """# A05:2025 Injection

## Description.
Untrusted input reaches an interpreter. CWE-89 applies.

## How to prevent.
Use parameterized queries.
""",
            "https://github.com/OWASP/Top10/blob/master/2025/docs/en/A05_2025-Injection.md",
        )

        self.assertEqual(parsed["category_id"], "A05:2025")
        self.assertEqual(parsed["year"], 2025)
        self.assertEqual(parsed["name"], "Injection")
        self.assertIn("parameterized queries", parsed["prevention"])
        self.assertEqual(parsed["cwe_ids"], ["CWE-89"])

    def test_keeps_release_year_in_historical_category_id(self):
        parsed = _parse_category(
            "A03_2021-Injection.md",
            "# A03:2021 Injection\n\n## Description\nSQL injection includes CWE-89.",
            "https://github.com/OWASP/Top10/blob/master/2021/docs/en/A03_2021-Injection.md",
        )

        self.assertEqual(parsed["category_id"], "A03:2021")
        self.assertEqual(parsed["year"], 2021)

    def test_exact_id_pattern_accepts_any_release_year(self):
        source = OwaspSource()

        self.assertEqual(source.extract_ids("See A05:2025 for details"), ["A05:2025"])
        self.assertEqual(source.extract_ids("Legacy A03_2021"), ["A03:2021"])


if __name__ == "__main__":
    unittest.main()
