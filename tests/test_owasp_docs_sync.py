import unittest
import hashlib

from app.ingestion.owasp_docs_sync import PROJECTS, _is_substantive, _parse_document


class OwaspDocsSyncTests(unittest.TestCase):
    def test_project_filters_only_authoritative_english_content(self):
        specs = {spec.key: spec for spec in PROJECTS}

        self.assertTrue(specs["cheat-sheets"].path_pattern.match("cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.md"))
        self.assertFalse(specs["cheat-sheets"].path_pattern.match("assets/example.md"))
        self.assertTrue(specs["wstg"].path_pattern.match("document/4-Web_Application_Security_Testing/07-Input_Validation_Testing/05-Testing_for_SQL_Injection.md"))
        self.assertFalse(specs["wstg"].path_pattern.match("translations/fr/test.md"))
        self.assertTrue(specs["asvs"].path_pattern.match("5.0/en/0x11-V2-Authentication.md"))
        self.assertFalse(specs["asvs"].path_pattern.match("5.0/fr/0x11-V2-Authentication.md"))
        self.assertTrue(specs["api-security"].path_pattern.match("editions/2023/en/0xa1-broken-object-level-authorization.md"))
        self.assertFalse(specs["api-security"].path_pattern.match("editions/2019/en/0xa1.md"))

    def test_document_parser_preserves_provenance_and_security_ids(self):
        spec = next(spec for spec in PROJECTS if spec.key == "cheat-sheets")
        parsed = _parse_document(
            spec,
            "cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.md",
            "a" * 40,
            "# SQL Injection Prevention Cheat Sheet\n\nUse prepared statements for CWE-89.",
        )

        self.assertEqual(parsed["project"], "cheat-sheets")
        self.assertEqual(parsed["title"], "SQL Injection Prevention Cheat Sheet")
        self.assertEqual(parsed["cwe_ids"], ["CWE-89"])
        self.assertEqual(parsed["git_sha"], "a" * 40)
        self.assertIn("OWASP/CheatSheetSeries/blob/master", parsed["source_url"])

    def test_git_blob_sha_is_reproducible_without_github_api(self):
        content = b"# Security guidance\n"
        git_sha = hashlib.sha1(
            f"blob {len(content)}\0".encode("ascii") + content
        ).hexdigest()

        self.assertEqual(git_sha, "d816e736833bd8e36341da7ba09226ab4b827ae2")

    def test_removed_and_navigation_only_documents_are_not_ingested(self):
        self.assertFalse(_is_substantive("# Old test\n\nThis content has been removed."))
        self.assertFalse(_is_substantive("# Reporting\n\n5.1 Structure\n\n5.2 Naming"))
        self.assertTrue(
            _is_substantive(
                "This guide explains how to test authentication controls, identify "
                "weak session handling, record evidence, and recommend remediation. "
                "It includes enough technical detail to support analyst retrieval."
            )
        )


if __name__ == "__main__":
    unittest.main()
