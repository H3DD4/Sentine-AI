import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.routers.chat import _generate_complete_response, _was_length_limited
from app.schemas import ChatRequest


class ContinuationTests(unittest.TestCase):
    def test_chat_request_accepts_manual_model(self):
        request = ChatRequest(
            messages=[{"role": "user", "content": "test"}],
            model="google/gemini-2.5-flash-lite",
        )
        self.assertEqual(request.model, "google/gemini-2.5-flash-lite")

    def test_provider_length_reasons_are_normalized(self):
        self.assertTrue(_was_length_limited("length"))
        self.assertTrue(_was_length_limited("max_tokens"))
        self.assertTrue(_was_length_limited("MAX_TOKENS"))
        self.assertFalse(_was_length_limited("stop"))

    def test_length_truncation_is_continued(self):
        client = AsyncMock()
        client.last_finish_reason = "length"
        parts = iter(["first part", "second part"])

        async def generate(*, messages, system, max_tokens):
            result = next(parts)
            client.last_finish_reason = "stop" if result == "second part" else "length"
            return result

        client.generate.side_effect = generate
        result = asyncio.run(
            _generate_complete_response(client, [{"role": "user", "content": "write"}], system="system")
        )

        self.assertEqual(result, "first partsecond part")
        self.assertEqual(client.generate.await_count, 2)
        continuation_messages = client.generate.await_args_list[1].kwargs["messages"]
        self.assertEqual(continuation_messages[-2]["role"], "assistant")
        self.assertIn("first part", continuation_messages[-2]["content"])

    def test_normal_stop_does_not_create_extra_request(self):
        client = AsyncMock()
        client.last_finish_reason = "stop"
        client.generate.return_value = "complete"

        result = asyncio.run(
            _generate_complete_response(client, [{"role": "user", "content": "write"}], system="system")
        )

        self.assertEqual(result, "complete")
        client.generate.assert_awaited_once()

    def test_continuation_count_is_bounded(self):
        client = AsyncMock()

        async def generate(*, messages, system, max_tokens):
            client.last_finish_reason = "length"
            return "part"

        client.generate.side_effect = generate
        with patch("app.routers.chat.settings.CHAT_MAX_CONTINUATIONS", 2):
            result = asyncio.run(
                _generate_complete_response(
                    client, [{"role": "user", "content": "write"}], system="system"
                )
            )

        self.assertEqual(result, "partpartpart")
        self.assertEqual(client.generate.await_count, 3)


if __name__ == "__main__":
    unittest.main()
