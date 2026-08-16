import asyncio
import unittest
from unittest.mock import patch

from app.kb.base import SearchOutcome
from app.routers.chat import chat_stream
from app.schemas import ChatMessage, ChatRequest
from app.services.chat_grounding import GroundedChatDraft, GroundingResult


async def _collect(response):
    return "".join([chunk async for chunk in response.body_iterator])


class ChatStreamPipelineTests(unittest.TestCase):
    def test_slow_private_generation_emits_keepalive_before_answer(self):
        async def slow_response(client, messages, outcome, **kwargs):
            stage = kwargs["stage"]
            await stage("draft", "Drafting analysis", "active", "Working")
            await asyncio.sleep(0.05)
            return "validated answer", []

        request = ChatRequest(messages=[ChatMessage(role="user", content="Analyze")])
        with patch(
            "app.routers.chat._conversation_outcome",
            return_value=SearchOutcome(query="Analyze"),
        ), patch(
            "app.routers.chat.generate_conversational_response",
            side_effect=slow_response,
        ), patch(
            "app.routers.chat.SSE_PIPELINE_POLL_SECONDS", 0.01,
        ), patch(
            "app.routers.chat.SSE_KEEPALIVE_SECONDS", 0.0,
        ):
            response = asyncio.run(chat_stream(request, session=object()))
            content = asyncio.run(_collect(response))

        self.assertIn(": grounding pipeline active\n\n", content)
        self.assertIn('"token": "validated answer"', content)
        self.assertLess(
            content.index(": grounding pipeline active"),
            content.index('"token": "validated answer"'),
        )

    def test_pipeline_exception_emits_failed_stage_and_bounded_error(self):
        async def broken_response(client, messages, outcome, **kwargs):
            raise ValueError("provider response contained sensitive internals")

        request = ChatRequest(messages=[ChatMessage(role="user", content="Analyze")])
        with patch(
            "app.routers.chat._conversation_outcome",
            return_value=SearchOutcome(query="Analyze"),
        ), patch(
            "app.routers.chat.generate_conversational_response",
            side_effect=broken_response,
        ):
            response = asyncio.run(chat_stream(request, session=object()))
            content = asyncio.run(_collect(response))

        self.assertIn('"status": "error"', content)
        self.assertIn("underlying ValueError", content)
        self.assertNotIn("sensitive internals", content)

    def test_selected_model_reaches_grounding_pipeline(self):
        received = {}

        async def selected_response(client, messages, outcome, **kwargs):
            received["model"] = kwargs.get("model")
            return "validated answer", GroundingResult(draft=GroundedChatDraft())

        request = ChatRequest(
            messages=[ChatMessage(role="user", content="Analyze")],
            model="selected/model",
        )
        with patch(
            "app.routers.chat.configured_chat_models",
            return_value=["selected/model"],
        ), patch(
            "app.routers.chat._conversation_outcome",
            return_value=SearchOutcome(query="Analyze"),
        ), patch(
            "app.routers.chat.generate_conversational_response",
            side_effect=selected_response,
        ):
            response = asyncio.run(chat_stream(request, session=object()))
            asyncio.run(_collect(response))

        self.assertEqual(received["model"], "selected/model")


if __name__ == "__main__":
    unittest.main()
