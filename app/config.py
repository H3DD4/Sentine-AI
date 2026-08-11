from enum import Enum
from pydantic_settings import BaseSettings


class LLMProvider(str, Enum):
    anthropic = "anthropic"
    openai = "openai"
    together = "together"
    openrouter = "openrouter"
    ollama = "ollama"
    gemini = "gemini"


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost:5432/redteam_rag"

    # Vector DB
    QDRANT_URL: str = "http://localhost:6333"
    # Legacy single-collection name — read only by the backfill script.
    # Retrieval uses one collection per source: kb_<source_key>.
    QDRANT_COLLECTION: str = "kb_entries"
    EMBEDDING_MODEL: str = "BAAI/bge-base-en-v1.5"
    RERANKER_MODEL: str = "BAAI/bge-reranker-base"
    # Cross-encoder reranking is the single biggest precision win in the
    # pipeline, but it costs ~50-150ms per query on CPU. Disable to trade
    # precision for latency.
    RERANK_ENABLED: bool = True
    RERANK_CANDIDATES: int = 15
    # CPU inference for 15 cross-encoder pairs takes well over three seconds on
    # typical analyst workstations. A timeout below that silently forces every
    # query onto the lower-precision fusion fallback.
    RERANK_TIMEOUT_SECONDS: float = 30.0
    # Whether model loading may reach the network.
    #
    # Off by default, and that default is deliberate: a model that is not in
    # the local cache otherwise triggers a multi-gigabyte download *inside the
    # first query that needs it*. That is not a slow query — HuggingFace has no
    # default timeout, so on a constrained link the request simply never
    # returns, and retrieval that could have been served from the local corpus
    # is blocked on a download nobody asked for. With this off, an uncached
    # model is treated exactly like a broken one: log it, degrade, keep
    # answering from what is already on disk.
    #
    # Turn it on for a deliberate, supervised warm-up (see
    # `scripts/warm_models.py`), not for serving.
    ALLOW_MODEL_DOWNLOADS: bool = False

    # LLM Provider Selection
    LLM_PROVIDER: LLMProvider = LLMProvider.anthropic
    LLM_REQUEST_TIMEOUT_SECONDS: float = 45.0
    CHAT_MAX_TOKENS: int = 6000
    CHAT_MAX_CONTINUATIONS: int = 2
    VISION_STAGE_TIMEOUT_SECONDS: float = 30.0
    VALIDATION_STAGE_TIMEOUT_SECONDS: float = 45.0

    # Anthropic
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_CHAT_MODEL: str = "claude-sonnet-4-20250514"
    ANTHROPIC_VALIDATION_MODEL: str = "claude-sonnet-4-20250514"
    ANTHROPIC_VISION_MODEL: str = "claude-sonnet-4-20250514"

    # OpenAI / OpenAI-compatible (GPT-4o, DeepSeek, Mistral via compatible endpoints)
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_CHAT_MODEL: str = "gpt-4o-mini"
    OPENAI_VALIDATION_MODEL: str = "gpt-4o-mini"
    OPENAI_VISION_MODEL: str = "gpt-4o-mini"

    # Together AI
    TOGETHER_API_KEY: str = ""
    TOGETHER_CHAT_MODEL: str = "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"
    TOGETHER_VALIDATION_MODEL: str = "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"
    TOGETHER_VISION_MODEL: str = "meta-llama/Llama-3.2-11B-Vision-Instruct-Turbo"

    # OpenRouter
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_CHAT_MODEL: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    OPENROUTER_VALIDATION_MODEL: str = "google/gemini-2.5-flash-lite"
    OPENROUTER_VISION_MODEL: str = "google/gemini-2.5-flash-lite"
    OPENROUTER_CHAT_FALLBACK_MODELS: list[str] = ["google/gemini-2.5-flash-lite"]
    OPENROUTER_VALIDATION_FALLBACK_MODELS: list[str] = []
    OPENROUTER_VISION_FALLBACK_MODELS: list[str] = [
        "nvidia/nemotron-nano-12b-v2-vl:free"
    ]
    LLM_MODEL_COOLDOWN_SECONDS: float = 120.0
    LLM_CAPACITY_RETRIES: int = 1

    # Ollama (local)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_CHAT_MODEL: str = "llama3.2"
    OLLAMA_VALIDATION_MODEL: str = "llama3.2"
    OLLAMA_VISION_MODEL: str = "llama3.2-vision"

    # Google Gemini
    GEMINI_API_KEY: str = ""
    GEMINI_CHAT_MODEL: str = "gemini-1.5-flash"
    GEMINI_VALIDATION_MODEL: str = "gemini-1.5-flash"
    GEMINI_VISION_MODEL: str = "gemini-1.5-flash"

    # Legacy shorthands (for backwards compatibility)
    @property
    def CHAT_MODEL(self) -> str:
        return getattr(self, f"{self.LLM_PROVIDER.value.upper()}_CHAT_MODEL")

    @property
    def VALIDATION_MODEL(self) -> str:
        return getattr(self, f"{self.LLM_PROVIDER.value.upper()}_VALIDATION_MODEL")

    @property
    def VISION_MODEL(self) -> str:
        return getattr(self, f"{self.LLM_PROVIDER.value.upper()}_VISION_MODEL")

    # NVD
    NVD_API_KEY: str = ""
    NVD_BASE_URL: str = "https://services.nvd.nist.gov/rest/json/cves/2.0"

    # MITRE ATT&CK
    MITRE_STIX_URL: str = "https://github.com/mitre-attack/attack-stix-data/releases/latest/download/enterprise-attack.json"

    # OWASP Top 10 (official project repository). The sync discovers the latest
    # published numeric release below this repository root.
    OWASP_TOP10_CONTENTS_URL: str = "https://api.github.com/repos/OWASP/Top10/contents"

    # Ghostwriter
    GHOSTWRITER_URL: str = ""
    GHOSTWRITER_API_KEY: str = ""
    GHOSTWRITER_VERIFY_TLS: bool = True

    # Auth (JWT)
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # App
    API_KEY: str = "change-me"
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:8003"]
    # Development-only convenience: allow any local browser port. Production
    # deployments should keep CORS_ORIGINS explicit and disable this flag.
    CORS_ALLOW_LOCALHOST: bool = True
    UPLOAD_DIR: str = "/tmp/redteam_evidence"
    REPORT_DIR: str = "/tmp/redteam_reports"
    REPORT_TEMPLATE_DIR: str = "/tmp/redteam_report_templates"
    EVIDENCE_MAX_FILES: int = 20
    EVIDENCE_MAX_FILE_BYTES: int = 50 * 1024 * 1024
    EVIDENCE_MAX_TOTAL_BYTES: int = 100 * 1024 * 1024
    EVIDENCE_UPLOAD_CHUNK_BYTES: int = 1024 * 1024

    class Config:
        env_file = ".env"

settings = Settings()
