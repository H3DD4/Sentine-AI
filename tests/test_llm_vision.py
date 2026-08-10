import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.config import LLMProvider
from app.services.llm_client import AsyncLLMClient


class VisionFallbackTests(unittest.TestCase):
    def test_openrouter_uses_fallback_after_primary_failure(self):
        client = AsyncLLMClient(provider=LLMProvider.openrouter)
        call = AsyncMock(
            side_effect=[
                RuntimeError("primary unavailable"),
                RuntimeError("primary unavailable"),
                "image details",
            ]
        )

        with patch(
            "app.services.llm_client._get_model",
            return_value="primary/model",
        ), patch(
            "app.services.llm_client.settings.OPENROUTER_VISION_FALLBACK_MODELS",
            ["fallback/model"],
        ), patch.object(
            client,
            "_describe_image_openai_compat",
            call,
        ):
            result = asyncio.run(client.describe_image(b"image", "image/jpeg"))

        self.assertEqual(result, "image details")
        self.assertEqual(call.await_count, 3)
        self.assertEqual(call.await_args_list[-1].args[2], "fallback/model")

    def test_duplicate_fallback_model_is_not_called_twice(self):
        client = AsyncLLMClient(provider=LLMProvider.openrouter)
        call = AsyncMock(return_value="image details")

        with patch(
            "app.services.llm_client._get_model",
            return_value="same/model",
        ), patch(
            "app.services.llm_client.settings.OPENROUTER_VISION_FALLBACK_MODELS",
            ["same/model"],
        ), patch.object(
            client,
            "_describe_image_openai_compat",
            call,
        ):
            result = asyncio.run(client.describe_image(b"image", "image/jpeg"))

        self.assertEqual(result, "image details")
        call.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
