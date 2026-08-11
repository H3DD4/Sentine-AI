import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ghostwriter_client import _graphql_url, get_findings, get_projects


class GhostwriterClientTests(unittest.TestCase):
    def test_graphql_url_uses_documented_endpoint(self):
        with patch("app.services.ghostwriter_client.settings.GHOSTWRITER_URL", "https://gw.test/"):
            self.assertEqual(_graphql_url(), "https://gw.test/v1/graphql")

    def test_graphql_url_preserves_full_endpoint(self):
        with patch(
            "app.services.ghostwriter_client.settings.GHOSTWRITER_URL",
            "https://gw.test/v1/graphql",
        ):
            self.assertEqual(_graphql_url(), "https://gw.test/v1/graphql")

    def test_projects_are_normalized_for_frontend(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {
                "project": [
                    {
                        "id": 7,
                        "codename": "Northwind",
                        "client": {"name": "ACME"},
                        "reports": [{"id": 9, "title": "External Test"}],
                    }
                ]
            }
        }
        client = AsyncMock()
        client.post.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client

        with patch("app.services.ghostwriter_client.httpx.AsyncClient", return_value=context), patch(
            "app.services.ghostwriter_client.settings.GHOSTWRITER_URL", "https://gw.test"
        ), patch(
            "app.services.ghostwriter_client.settings.GHOSTWRITER_API_KEY", "gwat_test"
        ):
            projects = asyncio.run(get_projects())

        self.assertEqual(
            projects,
            [
                {
                    "id": "9",
                    "title": "Northwind / External Test",
                    "client": {"name": "ACME"},
                }
            ],
        )
        self.assertEqual(client.post.call_args.args[0], "https://gw.test/v1/graphql")
        self.assertEqual(
            client.post.call_args.kwargs["headers"]["Authorization"], "Bearer gwat_test"
        )

    def test_graphql_errors_are_not_treated_as_connected(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"errors": [{"message": "access denied"}]}
        client = AsyncMock()
        client.post.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client

        with patch("app.services.ghostwriter_client.httpx.AsyncClient", return_value=context), patch(
            "app.services.ghostwriter_client.settings.GHOSTWRITER_URL", "https://gw.test"
        ), patch(
            "app.services.ghostwriter_client.settings.GHOSTWRITER_API_KEY", "bad-token"
        ):
            with self.assertRaisesRegex(ValueError, "access denied"):
                asyncio.run(get_projects())

    def test_public_schema_is_reported_as_an_authentication_problem(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "errors": [
                {"message": "field 'project' not found in type: 'query_root'"}
            ]
        }
        client = AsyncMock()
        client.post.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client

        with patch("app.services.ghostwriter_client.httpx.AsyncClient", return_value=context), patch(
            "app.services.ghostwriter_client.settings.GHOSTWRITER_URL", "https://gw.test"
        ), patch(
            "app.services.ghostwriter_client.settings.GHOSTWRITER_API_KEY", "legacy-key"
        ):
            with self.assertRaisesRegex(ValueError, "public GraphQL schema"):
                asyncio.run(get_projects())

    def test_findings_are_returned_from_library_query(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {"finding": [{"id": 1, "title": "SSRF"}]}
        }
        client = AsyncMock()
        client.post.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client

        with patch("app.services.ghostwriter_client.httpx.AsyncClient", return_value=context), patch(
            "app.services.ghostwriter_client.settings.GHOSTWRITER_URL", "https://gw.test"
        ), patch(
            "app.services.ghostwriter_client.settings.GHOSTWRITER_API_KEY", "gwat_test"
        ):
            findings = asyncio.run(get_findings())

        self.assertEqual(findings, [{"id": 1, "title": "SSRF"}])


if __name__ == "__main__":
    unittest.main()
