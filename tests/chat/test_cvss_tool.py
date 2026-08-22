import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app.services.cvss_tool import calculate_cvss
from app.services.mcp_tools import calculate_cvss_via_mcp, validate_draft_cvss_via_mcp


class CVSSDeterministicToolTests(unittest.TestCase):
    def test_calculates_and_canonicalizes_vector(self):
        result = calculate_cvss(
            "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:N/A:N"
        )

        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["score"], 7.7)
        self.assertEqual(result["severity"], "high")
        self.assertTrue(result["canonical_vector"].startswith("CVSS:3.1/"))

    def test_does_not_repair_invalid_vector(self):
        result = calculate_cvss("CVSS:3.1/AV:INVALID")

        self.assertEqual(result["status"], "invalid")
        self.assertIsNone(result["score"])
        self.assertEqual(result["canonical_vector"], "")

    def test_mcp_client_calls_named_calculator(self):
        async def fake_call(name, arguments):
            self.assertEqual(name, "calculate_cvss")
            self.assertEqual(arguments["vector"], "candidate")
            return type("Result", (), {
                "content": [type("Content", (), {
                    "text": '{"status":"invalid","score":null}'
                })()]
            })()

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def initialize(self):
                return None

            call_tool = staticmethod(fake_call)

        class FakeStdio:
            async def __aenter__(self):
                return object(), object()

            async def __aexit__(self, *args):
                return False

        async def run():
            with patch("app.services.mcp_tools.stdio_client", return_value=FakeStdio()), \
                    patch("app.services.mcp_tools.ClientSession", return_value=FakeSession()):
                return await calculate_cvss_via_mcp("candidate")

        result = __import__("asyncio").run(run())
        self.assertEqual(result["status"], "invalid")

    def test_model_candidate_is_sent_to_mcp(self):
        draft = SimpleNamespace(cvss=SimpleNamespace(
            status="exact",
            vector="CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:N/A:N",
        ))

        async def run():
            with patch(
                "app.services.mcp_tools.calculate_cvss_via_mcp",
                return_value={"status": "valid"},
            ) as calculate:
                issues = await validate_draft_cvss_via_mcp(
                    draft, explicit_vector_present=False
                )
                return issues, calculate

        issues, calculate = asyncio.run(run())
        self.assertEqual(issues, [])
        calculate.assert_awaited_once()

    def test_explicit_analyst_vector_does_not_need_model_candidate_mcp_call(self):
        draft = SimpleNamespace(cvss=SimpleNamespace(
            status="exact",
            vector="CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:N/A:N",
        ))

        async def run():
            with patch(
                "app.services.mcp_tools.calculate_cvss_via_mcp",
            ) as calculate:
                issues = await validate_draft_cvss_via_mcp(
                    draft, explicit_vector_present=True
                )
                return issues, calculate

        issues, calculate = asyncio.run(run())
        self.assertEqual(issues, [])
        calculate.assert_not_awaited()

    def test_empty_tool_request_is_detected_without_fabricating_arguments(self):
        from app.services.mcp_tools import extract_cvss_tool_request

        self.assertEqual(
            extract_cvss_tool_request('{"tool":"calculate_cvss","arguments":{}}'),
            {},
        )


if __name__ == "__main__":
    unittest.main()
