from fastapi import APIRouter, HTTPException
from app.config import settings as app_settings, LLMProvider
from app.schemas import SettingsOut, SettingsUpdate
import httpx

router = APIRouter(prefix="/settings", tags=["settings"])

# In-memory settings store (in production, use DB or Redis)
_runtime_settings = {
    "llm_provider": None,  # runtime override for provider selection
    "auto_validate": True,
    "auto_mitre_mapping": True,
    "anonymize_evidence": True,
    # Provider-level overrides
    "anthropic_chat_model": None,
    "anthropic_validation_model": None,
    "anthropic_vision_model": None,
    "openai_chat_model": None,
    "openai_validation_model": None,
    "openai_vision_model": None,
    "openai_base_url": None,
    "together_chat_model": None,
    "together_validation_model": None,
    "together_vision_model": None,
    "openrouter_chat_model": None,
    "openrouter_validation_model": None,
    "openrouter_vision_model": None,
    "ollama_base_url": None,
    "ollama_chat_model": None,
    "ollama_validation_model": None,
    "ollama_vision_model": None,
    "gemini_chat_model": None,
    "gemini_validation_model": None,
    "gemini_vision_model": None,
}

def _get_provider_model(key: str, default: str) -> str:
    """Get model name from runtime override or fall back to config default."""
    return _runtime_settings.get(key) or default

def _check_ollama_available() -> bool:
    """Quick check if Ollama is running locally."""
    try:
        resp = httpx.get(f"{app_settings.OLLAMA_BASE_URL}/api/tags", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False

def _get_active_provider() -> LLMProvider:
    """Get the effective provider, checking runtime override first."""
    runtime_provider = _runtime_settings.get("llm_provider")
    if runtime_provider is not None:
        return LLMProvider(runtime_provider)
    return app_settings.LLM_PROVIDER


# ── Public helpers used by llm_client.py ──────────────────────────────────────

def get_active_provider() -> LLMProvider:
    """Public alias — allows llm_client.py to get the current provider."""
    return _get_active_provider()


def get_provider_model(key: str, default: str) -> str:
    """Public alias — allows llm_client.py to get per-provider model overrides."""
    return _get_provider_model(key, default)


def _get_provider_base_url(key: str, default: str) -> str:
    """Get a base URL from runtime override or fall back to config default."""
    return _runtime_settings.get(key) or default


def get_provider_base_url(key: str, default: str) -> str:
    """Public alias — allows llm_client.py to honor runtime base-URL overrides
    (e.g. openai_base_url / ollama_base_url set via the Settings UI)."""
    return _get_provider_base_url(key, default)

@router.get("", response_model=SettingsOut)
async def get_settings():
    active_provider = _get_active_provider()
    return SettingsOut(
        llm_provider=active_provider.value,
        # Provider key status
        anthropic_api_key_set=bool(app_settings.ANTHROPIC_API_KEY),
        openai_api_key_set=bool(app_settings.OPENAI_API_KEY),
        together_api_key_set=bool(app_settings.TOGETHER_API_KEY),
        openrouter_api_key_set=bool(app_settings.OPENROUTER_API_KEY),
        ollama_available=_check_ollama_available(),
        gemini_api_key_set=bool(app_settings.GEMINI_API_KEY),
        # Provider-specific models
        anthropic_chat_model=_get_provider_model("anthropic_chat_model", app_settings.ANTHROPIC_CHAT_MODEL),
        anthropic_validation_model=_get_provider_model("anthropic_validation_model", app_settings.ANTHROPIC_VALIDATION_MODEL),
        anthropic_vision_model=_get_provider_model("anthropic_vision_model", app_settings.ANTHROPIC_VISION_MODEL),
        openai_chat_model=_get_provider_model("openai_chat_model", app_settings.OPENAI_CHAT_MODEL),
        openai_validation_model=_get_provider_model("openai_validation_model", app_settings.OPENAI_VALIDATION_MODEL),
        openai_vision_model=_get_provider_model("openai_vision_model", app_settings.OPENAI_VISION_MODEL),
        openai_base_url=_runtime_settings.get("openai_base_url") or app_settings.OPENAI_BASE_URL,
        together_chat_model=_get_provider_model("together_chat_model", app_settings.TOGETHER_CHAT_MODEL),
        together_validation_model=_get_provider_model("together_validation_model", app_settings.TOGETHER_VALIDATION_MODEL),
        together_vision_model=_get_provider_model("together_vision_model", app_settings.TOGETHER_VISION_MODEL),
        openrouter_chat_model=_get_provider_model("openrouter_chat_model", app_settings.OPENROUTER_CHAT_MODEL),
        openrouter_validation_model=_get_provider_model("openrouter_validation_model", app_settings.OPENROUTER_VALIDATION_MODEL),
        openrouter_vision_model=_get_provider_model("openrouter_vision_model", app_settings.OPENROUTER_VISION_MODEL),
        ollama_base_url=_runtime_settings.get("ollama_base_url") or app_settings.OLLAMA_BASE_URL,
        ollama_chat_model=_get_provider_model("ollama_chat_model", app_settings.OLLAMA_CHAT_MODEL),
        ollama_validation_model=_get_provider_model("ollama_validation_model", app_settings.OLLAMA_VALIDATION_MODEL),
        ollama_vision_model=_get_provider_model("ollama_vision_model", app_settings.OLLAMA_VISION_MODEL),
        gemini_chat_model=_get_provider_model("gemini_chat_model", app_settings.GEMINI_CHAT_MODEL),
        gemini_validation_model=_get_provider_model("gemini_validation_model", app_settings.GEMINI_VALIDATION_MODEL),
        gemini_vision_model=_get_provider_model("gemini_vision_model", app_settings.GEMINI_VISION_MODEL),
        # Legacy compat
        chat_model=_get_provider_model(f"{active_provider.value}_chat_model",
                                         getattr(app_settings, f"{active_provider.value.upper()}_CHAT_MODEL")),
        validation_model=_get_provider_model(f"{active_provider.value}_validation_model",
                                               getattr(app_settings, f"{active_provider.value.upper()}_VALIDATION_MODEL")),
        vision_model=_get_provider_model(f"{active_provider.value}_vision_model",
                                           getattr(app_settings, f"{active_provider.value.upper()}_VISION_MODEL")),
        # Shared
        nvd_api_key_set=bool(app_settings.NVD_API_KEY),
        ghostwriter_url_set=bool(app_settings.GHOSTWRITER_URL),
        embedding_model=app_settings.EMBEDDING_MODEL,
        cors_origins=app_settings.CORS_ORIGINS,
        auto_validate=_runtime_settings["auto_validate"],
        auto_mitre_mapping=_runtime_settings["auto_mitre_mapping"],
        anonymize_evidence=_runtime_settings["anonymize_evidence"],
    )

@router.patch("", response_model=SettingsOut)
async def update_settings(data: SettingsUpdate):
    # Provider selection
    if data.llm_provider is not None:
        try:
            _runtime_settings["llm_provider"] = data.llm_provider
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid provider: {data.llm_provider}")

    # Provider-specific model overrides
    provider_overrides = {
        "anthropic_chat_model": data.anthropic_chat_model,
        "anthropic_validation_model": data.anthropic_validation_model,
        "anthropic_vision_model": data.anthropic_vision_model,
        "openai_chat_model": data.openai_chat_model,
        "openai_validation_model": data.openai_validation_model,
        "openai_vision_model": data.openai_vision_model,
        "openai_base_url": data.openai_base_url,
        "together_chat_model": data.together_chat_model,
        "together_validation_model": data.together_validation_model,
        "together_vision_model": data.together_vision_model,
        "openrouter_chat_model": data.openrouter_chat_model,
        "openrouter_validation_model": data.openrouter_validation_model,
        "openrouter_vision_model": data.openrouter_vision_model,
        "ollama_base_url": data.ollama_base_url,
        "ollama_chat_model": data.ollama_chat_model,
        "ollama_validation_model": data.ollama_validation_model,
        "ollama_vision_model": data.ollama_vision_model,
        "gemini_chat_model": data.gemini_chat_model,
        "gemini_validation_model": data.gemini_validation_model,
        "gemini_vision_model": data.gemini_vision_model,
    }
    for key, value in provider_overrides.items():
        if value is not None:
            _runtime_settings[key] = value

    # Legacy shorthand support (map to active provider)
    active_provider = _get_active_provider()
    provider_prefix = active_provider.value
    if data.chat_model is not None:
        _runtime_settings[f"{provider_prefix}_chat_model"] = data.chat_model
    if data.validation_model is not None:
        _runtime_settings[f"{provider_prefix}_validation_model"] = data.validation_model
    if data.vision_model is not None:
        _runtime_settings[f"{provider_prefix}_vision_model"] = data.vision_model

    # Runtime toggles
    if data.auto_validate is not None:
        _runtime_settings["auto_validate"] = data.auto_validate
    if data.auto_mitre_mapping is not None:
        _runtime_settings["auto_mitre_mapping"] = data.auto_mitre_mapping
    if data.anonymize_evidence is not None:
        _runtime_settings["anonymize_evidence"] = data.anonymize_evidence

    return await get_settings()

# /settings/api-key intentionally removed — exposing the raw API key
# via an unauthenticated endpoint is a security risk.  The key is
# configured via the .env file and should not be read back over HTTP.
