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
import time
import threading
from typing import AsyncIterator, Optional

from app.config import LLMProvider, settings

log = logging.getLogger(__name__)

_model_circuits: dict[tuple[str, str], float] = {}
_model_probes: set[tuple[str, str]] = set()
_circuit_lock = threading.RLock()


def _is_retryable_provider_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status == 429 or isinstance(status, int) and 500 <= status < 600:
        return True
    text = str(exc).lower()
    markers = (
        "resourceexhausted",
        "resource exhausted",
        "request limit reached",
        "rate limit",
        "too many requests",
        "temporarily unavailable",
        "service unavailable",
        "worker unavailable",
        "worker local total request limit",
        "upstream timeout",
        "overloaded",
    )
    return any(marker in text for marker in markers)


def _model_available(provider: LLMProvider, model: str) -> bool:
    key = (provider.value, model)
    with _circuit_lock:
        open_until = _model_circuits.get(key, 0.0)
        if open_until and open_until <= time.monotonic():
            _model_circuits.pop(key, None)
            if key in _model_probes:
                return False
            _model_probes.add(key)
            return True
        return not open_until and key not in _model_probes


def _open_model_circuit(provider: LLMProvider, model: str) -> None:
    with _circuit_lock:
        _model_probes.discard((provider.value, model))
        _model_circuits[(provider.value, model)] = (
            time.monotonic() + settings.LLM_MODEL_COOLDOWN_SECONDS
        )


def _close_model_circuit(provider: LLMProvider, model: str) -> None:
    with _circuit_lock:
        _model_probes.discard((provider.value, model))
        _model_circuits.pop((provider.value, model), None)


def _model_chain(provider: LLMProvider, role: str, primary: str) -> list[str]:
    fallbacks = getattr(
        settings,
        f"{provider.value.upper()}_{role.upper()}_FALLBACK_MODELS",
        [],
    )
    return list(dict.fromkeys(model for model in [primary, *fallbacks] if model))


def configured_chat_models(provider: LLMProvider | None = None) -> list[str]:
    provider = provider or _get_active_provider()
    return _model_chain(provider, "chat", _get_model(provider, "chat"))

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
        self.last_generation: dict | None = None
        self.last_finish_reason: str | None = None

    def _record_generation(self, model: str, primary: str) -> None:
        self.last_generation = {
            "provider": self.provider.value,
            "model": model,
            "primary_model": primary,
            "fallback_used": model != primary,
        }

    # ── Internal client builders (lazy, not cached — async-safe) ──────────

    def _anthropic(self):
        import anthropic
        return anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )

    def _openai_compat(self, api_key: str, base_url: str):
        import openai
        return openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )

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
        primary = model or _get_model(p, "chat")
        models = [primary] if model else _model_chain(p, "chat", primary)
        return await self._generate_with_fallback(messages, system, max_tokens, models)

    async def _generate_with_fallback(
        self,
        messages: list[dict],
        system: str,
        max_tokens: int,
        models: list[str],
    ) -> str:
        last_error: Exception | None = None
        attempted = False
        primary = models[0]
        for model in models:
            if not _model_available(self.provider, model):
                continue
            attempted = True
            for attempt in range(settings.LLM_CAPACITY_RETRIES + 1):
                try:
                    result = await self._generate_model(messages, system, max_tokens, model)
                    _close_model_circuit(self.provider, model)
                    self._record_generation(model, primary)
                    return result
                except Exception as exc:
                    if not _is_retryable_provider_error(exc):
                        _close_model_circuit(self.provider, model)
                        raise
                    last_error = exc
                    if attempt < settings.LLM_CAPACITY_RETRIES:
                        await asyncio.sleep(0.5 * (attempt + 1))
            _open_model_circuit(self.provider, model)
            log.warning("Model %s is temporarily unavailable; trying fallback", model)

        if not attempted:
            raise RuntimeError("All configured models are cooling down")
        raise RuntimeError("All configured models are temporarily unavailable") from last_error

    async def _generate_model(
        self, messages: list[dict], system: str, max_tokens: int, model: str
    ) -> str:
        p = self.provider
        if p == LLMProvider.anthropic:
            return await self._generate_anthropic(messages, system, max_tokens, model)
        if p in (
            LLMProvider.openai,
            LLMProvider.together,
            LLMProvider.openrouter,
            LLMProvider.ollama,
        ):
            return await self._generate_openai_compat(messages, system, max_tokens, model, p)
        if p == LLMProvider.gemini:
            return await self._generate_gemini(messages, system, max_tokens, model)
        raise ValueError(f"Unknown provider: {p}")

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
        self.last_finish_reason = getattr(resp, "stop_reason", None)
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
        self.last_finish_reason = getattr(resp.choices[0], "finish_reason", None)
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
        candidate = resp.candidates[0] if getattr(resp, "candidates", None) else None
        reason = getattr(candidate, "finish_reason", None)
        self.last_finish_reason = getattr(reason, "name", reason)
        if isinstance(self.last_finish_reason, str):
            self.last_finish_reason = self.last_finish_reason.lower()
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
        primary = model or _get_model(p, "chat")
        models = [primary] if model else _model_chain(p, "chat", primary)
        last_error: Exception | None = None
        attempted = False
        primary_model = models[0]

        for candidate in models:
            if not _model_available(p, candidate):
                continue
            attempted = True
            emitted = False
            try:
                async for chunk in self._stream_model(
                    messages, system, max_tokens, candidate
                ):
                    if not emitted:
                        self._record_generation(candidate, primary_model)
                    emitted = True
                    yield chunk
                _close_model_circuit(p, candidate)
                if not emitted:
                    self._record_generation(candidate, primary_model)
                return
            except Exception as exc:
                if emitted or not _is_retryable_provider_error(exc):
                    _close_model_circuit(p, candidate)
                    raise
                last_error = exc
                _open_model_circuit(p, candidate)
                log.warning("Streaming model %s unavailable; trying fallback", candidate)

        if not attempted:
            raise RuntimeError("All configured models are cooling down")
        raise RuntimeError("All configured models are temporarily unavailable") from last_error

    async def _stream_model(
        self, messages: list[dict], system: str, max_tokens: int, model: str
    ) -> AsyncIterator[str]:
        p = self.provider
        if p == LLMProvider.anthropic:
            async for chunk in self._stream_anthropic(messages, system, max_tokens, model):
                yield chunk
            return
        if p in (
            LLMProvider.openai,
            LLMProvider.together,
            LLMProvider.openrouter,
            LLMProvider.ollama,
        ):
            async for chunk in self._stream_openai_compat(
                messages, system, max_tokens, model, p
            ):
                yield chunk
            return
        yield await self._generate_model(messages, system, max_tokens, model)

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
            final = await stream.get_final_message()
            self.last_finish_reason = getattr(final, "stop_reason", None)

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
        self.last_finish_reason = None
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
            finish_reason = getattr(chunk.choices[0], "finish_reason", None)
            if finish_reason:
                self.last_finish_reason = finish_reason

    # ── Validation generation (structured JSON output) ────────────────────

    async def generate_validation(
        self,
        system: str,
        user_message: str,
        max_tokens: int = 1500,
        model: Optional[str] = None,
        parse_attempts: int = 3,
    ) -> dict:
        """
        Generate validation result as a parsed dict.
        Uses structured output where available; retries on JSON parse failure.
        """
        p = self.provider
        primary = model or _get_model(p, "validation")
        models = [primary] if model else _model_chain(p, "validation", primary)

        # Bound before the loop so the failure log below is always reportable.
        # `generate` itself can raise ValueError (a refusal, an empty
        # completion, a provider error mapped to ValueError), in which case
        # `raw` was never assigned — and the handler's own `raw[:500]` then
        # raised UnboundLocalError, replacing the real cause with a traceback
        # about a missing local. The operator lost the one diagnostic the
        # handler existed to produce.
        raw: str = ""

        parse_attempts = max(1, min(int(parse_attempts), 3))
        for attempt in range(parse_attempts):
            try:
                raw = await self._generate_with_fallback(
                    [{"role": "user", "content": user_message}],
                    system,
                    max_tokens,
                    models,
                )
                return _extract_json(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                if attempt == parse_attempts - 1:
                    log.error(
                        "Validation JSON parse failed after %d attempt(s): %s\nRaw: %s",
                        parse_attempts, exc,
                        raw[:500] if raw else "<no completion returned>",
                    )
                    raise
                log.warning("JSON parse attempt %d failed, retrying…", attempt + 1)
                await asyncio.sleep(0.5)

        # Unreachable: the final attempt either returns or re-raises. Present so the
        # function has no implicit `None` path — a caller that got None here
        # would fail much later, far from the cause.
        raise RuntimeError("generate_validation exhausted retries without a result")

    # ── Vision (image description) ─────────────────────────────────────────

    async def describe_image(self, image_bytes: bytes, media_type: str) -> str:
        p = self.provider
        models = _model_chain(p, "vision", _get_model(p, "vision"))

        last_error: Exception | None = None
        for model in models:
            if not _model_available(p, model):
                continue
            try:
                async def _call():
                    if p == LLMProvider.anthropic:
                        return await self._describe_image_anthropic(image_bytes, media_type, model)
                    if p in (
                        LLMProvider.openai,
                        LLMProvider.together,
                        LLMProvider.openrouter,
                        LLMProvider.ollama,
                    ):
                        return await self._describe_image_openai_compat(
                            image_bytes, media_type, model, p
                        )
                    if p == LLMProvider.gemini:
                        return await self._describe_image_gemini(image_bytes, media_type, model)
                    raise ValueError(f"Vision not supported for provider: {p}")

                for attempt in range(settings.LLM_CAPACITY_RETRIES + 1):
                    try:
                        result = await _call()
                        _close_model_circuit(p, model)
                        return result
                    except Exception as exc:
                        if not _is_retryable_provider_error(exc):
                            _close_model_circuit(p, model)
                            raise
                        last_error = exc
                        if attempt < settings.LLM_CAPACITY_RETRIES:
                            await asyncio.sleep(0.5 * (attempt + 1))
                raise last_error
            except Exception as exc:
                if not _is_retryable_provider_error(exc):
                    raise
                last_error = exc
                _open_model_circuit(p, model)
                log.warning("Vision model %s failed: %s", model, exc)

        raise RuntimeError(
            f"All configured vision models failed for provider {p.value}"
        ) from last_error

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
