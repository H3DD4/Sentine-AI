from pydantic import BaseModel, field_serializer
from typing import Optional, List, Any
from datetime import datetime
from enum import Enum
from app.config import LLMProvider

class VerdictEnum(str, Enum):
    confirmed = "confirmed"
    likely = "likely"
    insufficient = "insufficient"
    false_positive = "false_positive"

class Token(BaseModel):
    access_token: str
    token_type: str

class UserCreate(BaseModel):
    email: str
    password: str

class UserOut(BaseModel):
    id: str
    email: str
    is_active: bool
    created_at: Optional[str] = None

    class Config:
        from_attributes = True

class ValidationResult(BaseModel):
    verdict: VerdictEnum
    confidence: float                    # 0.0 – 1.0
    reasoning: str
    matched_cves: List[str]
    matched_techniques: List[str]
    missing_evidence: List[str]
    recommended_next_steps: List[str]


class SourceStatus(BaseModel):
    """One knowledge source's contribution to a single answer."""
    source: str
    label: str
    status: str          # ok | no_match | empty | degraded | unavailable | disabled
    hits: int = 0
    latency_ms: int = 0
    detail: str = ""     # sentence safe to show the analyst verbatim
    searched_docs: Optional[int] = None


class RetrievedDoc(BaseModel):
    """A cited passage, always carrying the source it came from."""
    source: str
    source_label: str
    id: str
    title: str
    description: str
    score: float
    url: Optional[str] = None


class ValidationResponse(ValidationResult):
    """
    A verdict together with the provenance of the data behind it.

    The verdict alone is not auditable — this output reaches client
    deliverables, so which corpora were searched, which were unreachable, and
    which documents were actually cited all have to travel with it.
    """
    sources: List[SourceStatus] = []
    sources_used: List[str] = []
    provenance: str = ""
    degraded: bool = False
    citations: List[RetrievedDoc] = []
    finding_id: Optional[str] = None


class FindingCreate(BaseModel):
    title: str
    description: str

class FindingUpdate(BaseModel):
    analyst_confirmed: Optional[bool] = None

class EvidenceOut(BaseModel):
    id: str
    finding_id: str
    filename: str
    file_type: str
    storage_path: str
    extracted_text: Optional[str] = None
    image_description: Optional[str] = None
    uploaded_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class FindingOut(BaseModel):
    id: str
    title: str
    description: str
    verdict: Optional[VerdictEnum] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    matched_cves: List[str] = []
    matched_techniques: List[str] = []
    missing_evidence: List[str] = []
    recommended_next_steps: List[str] = []
    analyst_confirmed: bool = False
    ghostwriter_finding_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    evidence: List[EvidenceOut] = []

    @field_serializer("created_at", "updated_at", mode="plain", when_used="json")
    def _serialize_dt(self, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)

    class Config:
        from_attributes = True

class ChatMessage(BaseModel):
    role: str    # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    finding_id: Optional[str] = None
    action: Optional[str] = None
    title: Optional[str] = None

class KBEntryCreate(BaseModel):
    entry_type: str
    title: str
    description: str
    cvss_v3: Optional[float] = None
    mitre_techniques: List[str] = []
    affected_products: List[str] = []
    indicators: List[str] = []
    validation_steps: List[str] = []
    references: List[str] = []

class GhostwriterPushRequest(BaseModel):
    finding_id: str
    project_id: str

class ReportDraft(BaseModel):
    title: str = ""
    affected_scope: str = ""
    description: str = ""
    technical_evidence: str = ""
    reproduction_steps: List[str] = []
    impact: str = ""
    severity: str = ""
    cvss_score: Optional[float] = None
    cvss_vector: str = ""
    remediation: List[str] = []
    matched_cves: List[str] = []
    matched_techniques: List[str] = []
    verdict: Optional[VerdictEnum] = None
    confidence: Optional[float] = None


class ReportRequest(BaseModel):
    finding_ids: List[str]
    engagement_title: str
    client_name: str
    draft: Optional[ReportDraft] = None


class ReadinessDimension(BaseModel):
    key: str
    label: str
    score: float
    max_score: float
    complete: bool


class ReportReadinessResponse(BaseModel):
    score: float
    maximum: float = 10.0
    eligible: bool
    threshold: float
    status: str
    summary: str
    assessment_notice: Optional[str] = None
    strengths: List[str] = []
    missing: List[str] = []
    dimensions: List[ReadinessDimension] = []
    draft: ReportDraft


class EngagementCreate(BaseModel):
    client_name: str
    code: str
    scope: str
    lead: str
    status: str = "active"
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class EngagementUpdate(BaseModel):
    client_name: Optional[str] = None
    code: Optional[str] = None
    scope: Optional[str] = None
    lead: Optional[str] = None
    status: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

class EngagementOut(BaseModel):
    id: str
    client_name: str
    code: str
    scope: str
    progress: float
    findings_count: int
    lead: str
    status: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    created_at: str

    class Config:
        from_attributes = True

class SettingsOut(BaseModel):
    # Provider selection
    llm_provider: str = "anthropic"
    anthropic_api_key_set: bool = False
    openai_api_key_set: bool = False
    together_api_key_set: bool = False
    openrouter_api_key_set: bool = False
    ollama_available: bool = False
    gemini_api_key_set: bool = False

    # Provider-specific model configs
    anthropic_chat_model: str = "claude-sonnet-4-20250514"
    anthropic_validation_model: str = "claude-sonnet-4-20250514"
    anthropic_vision_model: str = "claude-sonnet-4-20250514"

    openai_chat_model: str = "gpt-4o-mini"
    openai_validation_model: str = "gpt-4o-mini"
    openai_vision_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    together_chat_model: str = "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"
    together_validation_model: str = "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"
    together_vision_model: str = "meta-llama/Llama-3.2-11B-Vision-Instruct-Turbo"

    openrouter_chat_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    openrouter_validation_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    openrouter_vision_model: str = "microsoft/phi-3-vision-128k-instruct:free"

    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "llama3.2"
    ollama_validation_model: str = "llama3.2"
    ollama_vision_model: str = "llama3.2-vision"

    gemini_chat_model: str = "gemini-1.5-flash"
    gemini_validation_model: str = "gemini-1.5-flash"
    gemini_vision_model: str = "gemini-1.5-flash"

    # Legacy fields (backwards compat)
    chat_model: str = "claude-sonnet-4-20250514"
    validation_model: str = "claude-sonnet-4-20250514"
    vision_model: str = "claude-sonnet-4-20250514"

    # Shared settings
    nvd_api_key_set: bool
    ghostwriter_url_set: bool
    embedding_model: str
    cors_origins: List[str]
    auto_validate: bool = True
    auto_mitre_mapping: bool = True
    anonymize_evidence: bool = True

class SettingsUpdate(BaseModel):
    llm_provider: Optional[str] = None

    # Anthropic model overrides
    anthropic_chat_model: Optional[str] = None
    anthropic_validation_model: Optional[str] = None
    anthropic_vision_model: Optional[str] = None

    # OpenAI model overrides
    openai_chat_model: Optional[str] = None
    openai_validation_model: Optional[str] = None
    openai_vision_model: Optional[str] = None
    openai_base_url: Optional[str] = None

    # Together model overrides
    together_chat_model: Optional[str] = None
    together_validation_model: Optional[str] = None
    together_vision_model: Optional[str] = None

    # OpenRouter model overrides
    openrouter_chat_model: Optional[str] = None
    openrouter_validation_model: Optional[str] = None
    openrouter_vision_model: Optional[str] = None

    # Ollama model overrides
    ollama_base_url: Optional[str] = None
    ollama_chat_model: Optional[str] = None
    ollama_validation_model: Optional[str] = None
    ollama_vision_model: Optional[str] = None

    # Gemini model overrides
    gemini_chat_model: Optional[str] = None
    gemini_validation_model: Optional[str] = None
    gemini_vision_model: Optional[str] = None

    # Legacy shorthands
    chat_model: Optional[str] = None
    validation_model: Optional[str] = None
    vision_model: Optional[str] = None

    # Shared toggles
    auto_validate: Optional[bool] = None
    auto_mitre_mapping: Optional[bool] = None
    anonymize_evidence: Optional[bool] = None

class AuditLogOut(BaseModel):
    id: str
    event_type: str
    finding_id: Optional[str] = None
    user_id: Optional[str] = None
    input_hash: str
    payload_summary: dict
    result_summary: dict
    timestamp: str

    class Config:
        from_attributes = True
