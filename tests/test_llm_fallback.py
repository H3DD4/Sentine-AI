import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.config import LLMProvider
from app.services.llm_client import AsyncLLMClient, _model_circuits, _model_probes


async def _collect(stream):
    return [chunk async for chunk in stream]


class LLMFallbackTests(unittest.TestCase):
    def setUp(self):
        _model_circuits.clear()
        _model_probes.clear()
        self.client = AsyncLLMClient(provider=LLMProvider.openrouter)

    def tearDown(self):
        _model_circuits.clear()
        _model_probes.clear()

    def test_capacity_error_falls_back(self):
        call = AsyncMock(
            side_effect=[
                RuntimeError("ResourceExhausted: Worker local total request limit reached"),
                "fallback response",
            ]
        )
        with patch.object(self.client, "_generate_model", call), patch(
            "app.services.llm_client._get_model", return_value="primary/model"
        ), patch(
            "app.services.llm_client.settings.OPENROUTER_CHAT_FALLBACK_MODELS",
            ["fallback/model"],
        ), patch(
            "app.services.llm_client.settings.LLM_CAPACITY_RETRIES", 0
        ):
            result = asyncio.run(self.client.generate([{"role": "user", "content": "test"}]))

        self.assertEqual(result, "fallback response")
        self.assertEqual(
            self.client.last_generation,
            {
                "provider": "openrouter",
                "model": "fallback/model",
                "primary_model": "primary/model",
                "fallback_used": True,
            },
        )
        self.assertEqual([item.args[3] for item in call.await_args_list], ["primary/model", "fallback/model"])

    def test_non_retryable_error_does_not_fall_back(self):
        call = AsyncMock(side_effect=ValueError("invalid request"))
        with patch.object(self.client, "_generate_model", call), patch(
            "app.services.llm_client._get_model", return_value="primary/model"
        ), patch(
            "app.services.llm_client.settings.OPENROUTER_CHAT_FALLBACK_MODELS",
            ["fallback/model"],
        ):
            with self.assertRaisesRegex(ValueError, "invalid request"):
                asyncio.run(self.client.generate([{"role": "user", "content": "test"}]))

        call.assert_awaited_once()

    def test_open_circuit_skips_primary_until_cooldown(self):
        call = AsyncMock(
            side_effect=[RuntimeError("request limit reached"), "first fallback"]
        )
        patches = (
            patch.object(self.client, "_generate_model", call),
            patch("app.services.llm_client._get_model", return_value="primary/model"),
            patch(
                "app.services.llm_client.settings.OPENROUTER_CHAT_FALLBACK_MODELS",
                ["fallback/model"],
            ),
            patch("app.services.llm_client.settings.LLM_CAPACITY_RETRIES", 0),
            patch("app.services.llm_client.settings.LLM_MODEL_COOLDOWN_SECONDS", 10),
            patch("app.services.llm_client.time.monotonic", return_value=100),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            asyncio.run(self.client.generate([{"role": "user", "content": "one"}]))
            call.reset_mock()
            call.return_value = "second fallback"
            call.side_effect = None
            result = asyncio.run(self.client.generate([{"role": "user", "content": "two"}]))

        self.assertEqual(result, "second fallback")
        self.assertEqual([item.args[3] for item in call.await_args_list], ["fallback/model"])

    def test_primary_is_probed_after_cooldown(self):
        _model_circuits[("openrouter", "primary/model")] = 110
        call = AsyncMock(return_value="primary recovered")
        with patch.object(self.client, "_generate_model", call), patch(
            "app.services.llm_client._get_model", return_value="primary/model"
        ), patch(
            "app.services.llm_client.settings.OPENROUTER_CHAT_FALLBACK_MODELS",
            ["fallback/model"],
        ), patch("app.services.llm_client.time.monotonic", return_value=111):
            result = asyncio.run(self.client.generate([{"role": "user", "content": "test"}]))

        self.assertEqual(result, "primary recovered")
        self.assertFalse(self.client.last_generation["fallback_used"])
        self.assertEqual(self.client.last_generation["model"], "primary/model")
        self.assertEqual(call.await_args.args[3], "primary/model")

    def test_stream_falls_back_only_before_output(self):
        calls = []

        async def stream_model(messages, system, max_tokens, model):
            calls.append(model)
            if model == "primary/model":
                raise RuntimeError("ResourceExhausted: request limit reached")
            yield "fallback stream"

        with patch.object(self.client, "_stream_model", stream_model), patch(
            "app.services.llm_client._get_model", return_value="primary/model"
        ), patch(
            "app.services.llm_client.settings.OPENROUTER_CHAT_FALLBACK_MODELS",
            ["fallback/model"],
        ):
            chunks = asyncio.run(_collect(self.client.generate_stream([])))

        self.assertEqual(chunks, ["fallback stream"])
        self.assertTrue(self.client.last_generation["fallback_used"])
        self.assertEqual(self.client.last_generation["model"], "fallback/model")
        self.assertEqual(calls, ["primary/model", "fallback/model"])

    def test_stream_does_not_fallback_after_partial_output(self):
        calls = []

        async def stream_model(messages, system, max_tokens, model):
            calls.append(model)
            yield "partial"
            raise RuntimeError("ResourceExhausted: request limit reached")

        with patch.object(self.client, "_stream_model", stream_model), patch(
            "app.services.llm_client._get_model", return_value="primary/model"
        ), patch(
            "app.services.llm_client.settings.OPENROUTER_CHAT_FALLBACK_MODELS",
            ["fallback/model"],
        ):
            with self.assertRaisesRegex(RuntimeError, "ResourceExhausted"):
                asyncio.run(_collect(self.client.generate_stream([])))

        self.assertEqual(calls, ["primary/model"])


if __name__ == "__main__":
    unittest.main()
