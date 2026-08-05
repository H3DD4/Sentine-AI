"""
Sentinel.AI — Unified Async LLM Client Factory
Supports: Anthropic, OpenAI, Together AI, OpenRouter, Ollama, Google Gemini

KEY CHANGE: All methods are now `async` and use async SDK clients so they
never block the FastAPI event loop.  The old sync `LLMClient` singleton
was the primary cause of the frontend feeling laggy.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import AsyncIterator, Optional

from app.config import LLMProvider, settings

log = logging.getLogger(__name__)

# ── Retry helpers ─────────────────────────────────────────────────────────────

_RETRY_EXCEPTIONS = (Exception,)  # tightened per-provider below


async def _with_retry(coro_fn, *, retries: int = 3, base_delay: float = 1.0):
    """Exponential-backoff retry wrapper."""
    for attempt in range(retries):
        try:
            return await coro_fn()
        except Exception as exc:
            if attempt == retries - 1:
                raise
            delay = base_delay * (2**attempt)
            log.warning("LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1, retries, exc, delay)
            await asyncio.sleep(delay)


# ── JSON extraction ───────────────────────────────────────────────────────────

def _extract_json(raw: str) -> dict:
    """Strip markdown fences and extract the first JSON object from a string."""
    # Remove ```json ... ``` fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw.strip())
    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Find first {...} block
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        return json.loads(match.group())
    raise json.JSONDecodeError("No JSON object found in LLM response", raw, 0)


# ── Provider resolver (reads runtime settings) ───────────────────────────────

def _get_active_provider() -> LLMProvider:
    """Always resolves the current provider including runtime overrides."""
    # Import here to avoid circular; settings.py stores runtime overrides
    try:
        from app.routers.settings import get_active_provider as _rt
        return _rt()
    except Exception:
        return settings.LLM_PROVIDER


def _get_model(provider: LLMProvider, role: str) -> str:
    """Get the effective model for a given role (chat/validation/vision)."""
    try:
        from app.routers.settings import get_provider_model as _rt
        key = f"{provider.value}_{role}_model"
        default = getattr(settings, f"{provider.value.upper()}_{role.upper()}_MODEL")
        return _rt(key, default)
    except Exception:
        return getattr(settings, f"{provider.value.upper()}_{role.upper()}_MODEL")


def _get_openai_base_url() -> str:
    """Effective OpenAI-compatible base URL (honors runtime Settings override)."""
    try:
        from app.routers.settings import get_provider_base_url as _rt
        return _rt("openai_base_url", settings.OPENAI_BASE_URL)
    except Exception:
        return settings.OPENAI_BASE_URL


def _get_ollama_base_url() -> str:
    """Effective Ollama base URL (honors runtime Settings override)."""
    try:
        from app.routers.settings import get_provider_base_url as _rt
        return _rt("ollama_base_url", settings.OLLAMA_BASE_URL)
    except Exception:
        return settings.OLLAMA_BASE_URL


# ── Main async client class ───────────────────────────────────────────────────

class AsyncLLMClient:
    """
    Unified async interface for all LLM providers.
    Instantiate fresh per request (lightweight — just stores provider enum).
    """

    def __init__(self, provider: Optional[LLMProvider] = None):
        self.provider = provider or _get_active_provider()

    # ── Internal client builders (lazy, not cached — async-safe) ──────────

    def _anthropic(self):
        import anthropic
        return anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    def _openai_compat(self, api_key: str, base_url: str):
        import openai
        return openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

    def _together(self):
        return self._openai_compat(
            settings.TOGETHER_API_KEY, "https://api.together.xyz/v1"
        )

    def _openrouter(self):
        return self._openai_compat(
            settings.OPENROUTER_API_KEY, "https://openrouter.ai/api/v1"
        )

    def _ollama(self):
        return self._openai_compat(
            "ollama", f"{_get_ollama_base_url()}/v1"
        )

    def _gemini(self):
        import google.genai as genai
        return genai.Client(api_key=settings.GEMINI_API_KEY)

    # ── Chat generation ────────────────────────────────────────────────────

    async def generate(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 1200,
        model: Optional[str] = None,
    ) -> str:
        p = self.provider
        m = model or _get_model(p, "chat")

        async def _call():
            if p == LLMProvider.anthropic:
                return await self._generate_anthropic(messages, system, max_tokens, m)
            elif p in (LLMProvider.openai, LLMProvider.together,
                       LLMProvider.openrouter, LLMProvider.ollama):
                return await self._generate_openai_compat(messages, system, max_tokens, m, p)
            elif p == LLMProvider.gemini:
                return await self._generate_gemini(messages, system, max_tokens, m)
            else:
                raise ValueError(f"Unknown provider: {p}")

        return await _with_retry(_call)

    async def _generate_anthropic(
        self, messages: list[dict], system: str, max_tokens: int, model: str
    ) -> str:
        client = self._anthropic()
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
        )
        return resp.content[0].text

    async def _generate_openai_compat(
        self, messages: list[dict], system: str, max_tokens: int,
        model: str, provider: LLMProvider
    ) -> str:
        if provider == LLMProvider.openai:
            client = self._openai_compat(settings.OPENAI_API_KEY, _get_openai_base_url())
        elif provider == LLMProvider.together:
            client = self._together()
        elif provider == LLMProvider.openrouter:
            client = self._openrouter()
        else:  # ollama
            client = self._ollama()

        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend({"role": m["role"], "content": m["content"]} for m in messages)

        resp = await client.chat.completions.create(
            model=model,
            messages=full_messages,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    async def _generate_gemini(
        self, messages: list[dict], system: str, max_tokens: int, model: str
    ) -> str:
        import google.genai.types as gtypes
        client = self._gemini()
        gemini_messages = []
        for m in messages:
            role = "user" if m["role"] == "user" else "model"
            gemini_messages.append({"role": role, "parts": [m["content"]]})
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=gemini_messages,
            config={"max_output_tokens": max_tokens, "system_instruction": system},
        )
        return resp.text

    # ── Streaming chat generation ──────────────────────────────────────────

    async def generate_stream(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 1200,
        model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Yield text chunks for SSE streaming."""
        p = self.provider
        m = model or _get_model(p, "chat")

        if p == LLMProvider.anthropic:
            async for chunk in self._stream_anthropic(messages, system, max_tokens, m):
                yield chunk
        elif p in (LLMProvider.openai, LLMProvider.together,
                   LLMProvider.openrouter, LLMProvider.ollama):
            async for chunk in self._stream_openai_compat(messages, system, max_tokens, m, p):
                yield chunk
        else:
            # Gemini / fallback: non-streaming
            text = await self.generate(messages, system, max_tokens, m)
            yield text

    async def _stream_anthropic(
        self, messages: list[dict], system: str, max_tokens: int, model: str
    ) -> AsyncIterator[str]:
        client = self._anthropic()
        async with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def _stream_openai_compat(
        self, messages: list[dict], system: str, max_tokens: int,
        model: str, provider: LLMProvider
    ) -> AsyncIterator[str]:
        if provider == LLMProvider.openai:
            client = self._openai_compat(settings.OPENAI_API_KEY, _get_openai_base_url())
        elif provider == LLMProvider.together:
            client = self._together()
        elif provider == LLMProvider.openrouter:
            client = self._openrouter()
        else:
            client = self._ollama()

        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend({"role": m["role"], "content": m["content"]} for m in messages)

        stream = await client.chat.completions.create(
            model=model,
            messages=full_messages,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    # ── Validation generation (structured JSON output) ────────────────────

    async def generate_validation(
        self,
        system: str,
        user_message: str,
        max_tokens: int = 1500,
    ) -> dict:
        """
        Generate validation result as a parsed dict.
        Uses structured output where available; retries on JSON parse failure.
        """
        p = self.provider
        m = _get_model(p, "validation")

        # Bound before the loop so the failure log below is always reportable.
        # `generate` itself can raise ValueError (a refusal, an empty
        # completion, a provider error mapped to ValueError), in which case
        # `raw` was never assigned — and the handler's own `raw[:500]` then
        # raised UnboundLocalError, replacing the real cause with a traceback
        # about a missing local. The operator lost the one diagnostic the
        # handler existed to produce.
        raw: str = ""

        for attempt in range(3):
            try:
                raw = await self.generate(
                    messages=[{"role": "user", "content": user_message}],
                    system=system,
                    max_tokens=max_tokens,
                    model=m,
                )
                return _extract_json(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                if attempt == 2:
                    log.error(
                        "Validation JSON parse failed after 3 attempts: %s\nRaw: %s",
                        exc,
                        raw[:500] if raw else "<no completion returned>",
                    )
                    raise
                log.warning("JSON parse attempt %d failed, retrying…", attempt + 1)
                await asyncio.sleep(0.5)

        # Unreachable: attempt 2 either returns or re-raises. Present so the
        # function has no implicit `None` path — a caller that got None here
        # would fail much later, far from the cause.
        raise RuntimeError("generate_validation exhausted retries without a result")

    # ── Vision (image description) ─────────────────────────────────────────

    async def describe_image(self, image_bytes: bytes, media_type: str) -> str:
        p = self.provider
        m = _get_model(p, "vision")

        async def _call():
            if p == LLMProvider.anthropic:
                return await self._describe_image_anthropic(image_bytes, media_type, m)
            elif p in (LLMProvider.openai, LLMProvider.together,
                       LLMProvider.openrouter, LLMProvider.ollama):
                return await self._describe_image_openai_compat(image_bytes, media_type, m, p)
            elif p == LLMProvider.gemini:
                return await self._describe_image_gemini(image_bytes, media_type, m)
            else:
                return "(Vision not supported for this provider)"

        return await _with_retry(_call)

    _VISION_PROMPT = (
        "You are a security analyst. Extract all text visible in this image "
        "and describe what security-relevant information it shows "
        "(e.g. error messages, version strings, HTTP responses, terminal output, "
        "network captures, CVE references). Be specific and complete."
    )

    async def _describe_image_anthropic(
        self, image_bytes: bytes, media_type: str, model: str
    ) -> str:
        b64 = base64.b64encode(image_bytes).decode()
        client = self._anthropic()
        resp = await client.messages.create(
            model=model,
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text", "text": self._VISION_PROMPT},
                ],
            }],
        )
        return resp.content[0].text

    async def _describe_image_openai_compat(
        self, image_bytes: bytes, media_type: str, model: str, provider: LLMProvider
    ) -> str:
        b64 = base64.b64encode(image_bytes).decode()
        if provider == LLMProvider.openai:
            client = self._openai_compat(settings.OPENAI_API_KEY, _get_openai_base_url())
        elif provider == LLMProvider.together:
            client = self._together()
        elif provider == LLMProvider.openrouter:
            client = self._openrouter()
        else:
            client = self._ollama()

        resp = await client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": self._VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                ],
            }],
            max_tokens=800,
        )
        return resp.choices[0].message.content or ""

    async def _describe_image_gemini(
        self, image_bytes: bytes, media_type: str, model: str
    ) -> str:
        import google.genai.types as gtypes
        client = self._gemini()
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=[
                gtypes.Content(
                    role="user",
                    parts=[
                        gtypes.Part.from_bytes(data=image_bytes, mime_type=media_type),
                        gtypes.Part.from_text(self._VISION_PROMPT),
                    ],
                )
            ],
        )
        return resp.text


# ── Backwards-compat shim ─────────────────────────────────────────────────────
# Old code that imports `LLMClient` still works — it's just an alias now.
LLMClient = AsyncLLMClient
