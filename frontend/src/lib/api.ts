/**
 * Sentinel.AI — Typed API Client
 * Connects the frontend to the FastAPI backend.
 * All functions use fetch() and return typed responses.
 *
 * Changes:
 * - API_BASE uses relative URLs when served from FastAPI and supports a
 *   VITE_API_BASE_URL override for development
 * - Added streamChat() for SSE streaming
 * - Added request abort controller support
 * - getApiKey() removed (endpoint was a security risk — removed from backend)
 */

// Vite runs separately in development; deployed builds use the current origin.
const configuredApiBase = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "");
const API_BASE =
  configuredApiBase ??
  (typeof window !== "undefined" && window.location.port !== "8000" && window.location.port !== ""
    ? "http://localhost:8000"
    : "");

// ─── Types ────────────────────────────────────────────────────────────

export type Verdict = "confirmed" | "likely" | "insufficient" | "false_positive";
export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type EngagementStatus = "active" | "reporting" | "scoping";

export interface Evidence {
  id: string;
  finding_id: string;
  filename: string;
  file_type: string;
  storage_path: string;
  extracted_text: string | null;
  image_description: string | null;
  uploaded_at: string | null;
}

export interface Finding {
  id: string;
  title: string;
  description: string;
  verdict: Verdict | null;
  confidence: number | null;
  reasoning: string | null;
  affected_scope: string | null;
  technical_evidence: string | null;
  reproduction_steps: string[];
  impact: string | null;
  severity: string | null;
  cvss_score: number | null;
  cvss_vector: string | null;
  matched_cves: string[];
  matched_techniques: string[];
  missing_evidence: string[];
  recommended_next_steps: string[];
  analyst_confirmed: boolean;
  ghostwriter_finding_id: string | null;
  engagement_id: string | null;
  created_at: string | null;
  updated_at: string | null;
  evidence: Evidence[];
}

export interface Engagement {
  id: string;
  client_name: string;
  code: string;
  scope: string;
  progress: number;
  findings_count: number;
  lead: string;
  status: EngagementStatus;
  start_date: string | null;
  end_date: string | null;
  created_at: string;
}

/**
 * A knowledge-base document as returned by GET /kb/entries.
 *
 * The backend renders these with the same payload builder that produces the
 * retrieval citations, so a document looks identical whether you browsed to it
 * or the RAG cited it. Sources carry different fields (a CVE has CVSS, an
 * ATT&CK technique has tactics), hence the optional tail.
 */
export interface KBEntry {
  source: string;
  doc_id: string;
  title: string;
  description: string;
  synced?: boolean;

  // NVD
  cvss_v3?: number;
  severity?: string;
  cwe?: string;
  attack_vector?: string;
  affected_products?: string[];
  ref_urls?: string[];
  published_date?: string;
  last_modified?: string;

  // MITRE / internal
  mitre_techniques?: string[];
  tactics?: string[];
  doc_type?: string;
  tags?: string[];
  indicators?: string[];
  validation_steps?: string[];

  // Search results only
  score?: number;
}

/** Payload for POST /kb/entries — always lands in the `internal` source. */
export interface KBEntryInput {
  entry_type: string;
  title: string;
  description: string;
  cvss_v3?: number | null;
  mitre_techniques?: string[];
  affected_products?: string[];
  indicators?: string[];
  validation_steps?: string[];
  references?: string[];
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

// ─── Source provenance ────────────────────────────────────────────────
// Every answer carries the record of which knowledge sources produced it.
// These types mirror app/kb/base.py — keep them in step.

export type SourceStatus =
  | "ok" // searched, contributed results
  | "no_match" // searched, nothing matched
  | "empty" // nothing indexed yet
  | "degraded" // searched, but coverage is partial
  | "unavailable" // could not be searched
  | "disabled"; // switched off by an operator

/** A source's contribution to one specific answer. */
export interface SourceReport {
  source: string;
  label: string;
  status: SourceStatus;
  hits: number;
  latency_ms: number;
  detail: string;
  searched_docs?: number | null;
}

/** A source's overall operational state, from GET /kb/sources. */
export interface SourceHealth {
  source: string;
  label: string;
  collection: string;
  table: string;
  status: SourceStatus;
  enabled: boolean;
  detail: string;
  row_count: number;
  vector_count: number;
  unsynced_count: number;
  weight: number;
  checked_at: string;
}

export interface SourcesResponse {
  sources: SourceHealth[];
  usable_count: number;
  total_count: number;
  retrieval_available: boolean;
}

export interface Citation {
  source: string;
  source_label: string;
  id: string;
  title: string;
  description: string;
  score: number;
  url: string | null;
}

/** Attached to every chat/validation response. */
export interface Provenance {
  sources: SourceReport[];
  sources_used: string[];
  provenance: string;
  degraded: boolean;
  citations: Citation[];
}

export interface ProcessingFile {
  filename: string;
  file_type: string;
  size_bytes: number;
  extracted_chars: number;
  selected_chars: number;
  needs_manual_review: boolean;
  notices: string[];
}

export interface ProcessingManifest {
  files: ProcessingFile[];
  total_bytes: number;
  notices: string[];
}

export interface ChatResponse extends Partial<Provenance> {
  response: string;
  processing?: ProcessingManifest;
}

export interface ValidationResult {
  verdict: Verdict;
  confidence: number;
  reasoning: string;
  matched_cves: string[];
  matched_techniques: string[];
  missing_evidence: string[];
  recommended_next_steps: string[];
}

export type ValidationResponse = ValidationResult &
  Partial<Provenance> & {
    finding_id?: string | null;
    processing?: ProcessingManifest | null;
  };

export interface ReportDraft {
  title: string;
  affected_scope: string;
  description: string;
  technical_evidence: string;
  reproduction_steps: string[];
  impact: string;
  severity: string;
  cvss_score: number | null;
  cvss_vector: string;
  remediation: string[];
  matched_cves: string[];
  matched_techniques: string[];
  verdict: Verdict | null;
  confidence: number | null;
}

export interface ReadinessDimension {
  key: string;
  label: string;
  score: number;
  max_score: number;
  complete: boolean;
}

export interface ReportReadiness {
  score: number;
  maximum: number;
  eligible: boolean;
  threshold: number;
  status: "not_ready" | "reportable" | "ready";
  summary: string;
  assessment_notice?: string | null;
  strengths: string[];
  missing: string[];
  dimensions: ReadinessDimension[];
  draft: ReportDraft;
}

export interface ConversationReportHandoff {
  readiness: ReportReadiness;
  messages: ChatMessage[];
  finding_id?: string | null;
}

export const REPORT_HANDOFF_STORAGE_KEY = "sentinel.report.handoff.v1";

export interface GhostwriterProject {
  id: string;
  title: string;
  client: { name: string };
}

export interface ReportRequest {
  finding_ids: string[];
  engagement_title: string;
  client_name: string;
  draft?: ReportDraft;
  template_id?: string;
  sections: ReportSection[];
}

export type ReportSection =
  | "executive_summary"
  | "scope_methodology"
  | "findings_overview"
  | "detailed_findings"
  | "attack_mapping"
  | "evidence_gaps"
  | "disclaimer";

export interface ReportTemplate {
  id: string;
  name: string;
  size_bytes: number;
  is_active: boolean;
  created_at: string;
}

export interface GeneratedReport {
  id: string;
  client_name: string;
  engagement_title: string;
  filename: string;
  finding_snapshot: Array<{ id: string; title: string; verdict: Verdict | null }>;
  draft_snapshot: ReportDraft | null;
  created_at: string;
}

export interface AppSettings {
  llm_provider: string;
  anthropic_api_key_set: boolean;
  openai_api_key_set: boolean;
  together_api_key_set: boolean;
  openrouter_api_key_set: boolean;
  ollama_available: boolean;
  gemini_api_key_set: boolean;
  anthropic_chat_model: string;
  anthropic_validation_model: string;
  anthropic_vision_model: string;
  openai_chat_model: string;
  openai_validation_model: string;
  openai_vision_model: string;
  openai_base_url: string;
  together_chat_model: string;
  together_validation_model: string;
  together_vision_model: string;
  openrouter_chat_model: string;
  openrouter_validation_model: string;
  openrouter_vision_model: string;
  ollama_base_url: string;
  ollama_chat_model: string;
  ollama_validation_model: string;
  ollama_vision_model: string;
  gemini_chat_model: string;
  gemini_validation_model: string;
  gemini_vision_model: string;
  chat_model: string;
  validation_model: string;
  vision_model: string;
  nvd_api_key_set: boolean;
  ghostwriter_url_set: boolean;
  embedding_model: string;
  cors_origins: string[];
  auto_validate: boolean;
  auto_mitre_mapping: boolean;
  anonymize_evidence: boolean;
}

export type Settings = AppSettings;

// ─── Helpers ──────────────────────────────────────────────────────────

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function responseError(res: Response): Promise<string> {
  const text = await res.text().catch(() => "");
  if (!text) return `HTTP ${res.status}`;
  try {
    const payload = JSON.parse(text) as { detail?: unknown };
    if (typeof payload.detail === "string") return payload.detail;
  } catch {
    // The backend may intentionally return plain text.
  }
  return text;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const headers: Record<string, string> = {};

  if (body && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(url, {
    method,
    headers,
    body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    signal,
  });

  if (!res.ok) {
    throw new ApiError(await responseError(res), res.status);
  }

  // Binary response (DOCX download)
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/") && !contentType.includes("json")) {
    return res.blob() as unknown as T;
  }

  // Empty (204 No Content)
  if (res.status === 204) {
    return undefined as unknown as T;
  }

  return res.json();
}

// ─── Chat ─────────────────────────────────────────────────────────────

export async function sendChatMessage(
  messages: ChatMessage[],
  findingId?: string,
): Promise<ChatResponse> {
  return request<ChatResponse>("POST", "/chat", { messages, finding_id: findingId });
}

/**
 * SSE streaming chat. Calls the callback with each token chunk.
 *
 * The backend sends a provenance frame before the first token, so `onSources`
 * fires early and the UI can show which corpora the answer rests on while it
 * is still streaming.
 *
 * Returns a cleanup function that aborts the stream.
 */
export function streamChatMessage(
  messages: ChatMessage[],
  onToken: (token: string) => void,
  onDone: () => void,
  onError: (err: Error) => void,
  findingId?: string,
  onSources?: (provenance: Provenance) => void,
  onAbort?: () => void,
): () => void {
  const controller = new AbortController();
  let manuallyAborted = false;
  let timedOut = false;
  let settled = false;
  let timeout = 0;

  const armTimeout = (delay: number) => {
    window.clearTimeout(timeout);
    timeout = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, delay);
  };
  armTimeout(90_000);

  const finish = (callback: () => void) => {
    if (settled) return;
    settled = true;
    window.clearTimeout(timeout);
    callback();
  };

  (async () => {
    try {
      const res = await fetch(`${API_BASE}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages, finding_id: findingId }),
        signal: controller.signal,
      });

      if (!res.ok) {
        throw new ApiError(await responseError(res), res.status);
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        // Once the server is responding, only abort a genuinely stalled stream.
        armTimeout(60_000);

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          let payload: Record<string, unknown>;
          try {
            payload = JSON.parse(line.slice(6));
          } catch (parseErr) {
            // A malformed frame should not kill an otherwise healthy stream.
            console.error("[SSE] unparseable frame", parseErr);
            continue;
          }
          // Errors are raised outside the try above, so a genuine backend
          // error is not swallowed by the JSON-parse handler.
          if (payload.error) throw new Error(String(payload.error));
          if (payload.done) {
            finish(onDone);
            return;
          }
          if (payload.sources && onSources) {
            onSources(payload as unknown as Provenance);
          }
          if (payload.token) onToken(String(payload.token));
        }
      }
      finish(onDone);
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        finish(() => {
          if (manuallyAborted) onAbort?.();
          else if (timedOut)
            onError(new Error("The chat stream stopped responding. Please try again."));
          else onError(new Error("The chat request was interrupted. Please try again."));
        });
      } else {
        finish(() => onError(err instanceof Error ? err : new Error(String(err))));
      }
    }
  })();

  return () => {
    manuallyAborted = true;
    controller.abort();
  };
}

// ─── Validation ───────────────────────────────────────────────────────

export async function validateFinding(
  title: string,
  description: string,
  files?: File[],
): Promise<ValidationResponse> {
  const formData = new FormData();
  formData.append("title", title);
  formData.append("description", description);
  if (files) {
    for (const file of files) {
      formData.append("files", file);
    }
  }
  return request<ValidationResponse>("POST", "/validate", formData);
}

// ─── Findings ─────────────────────────────────────────────────────────

export async function listFindings(skip = 0, limit = 50): Promise<Finding[]> {
  return request<Finding[]>("GET", `/findings?skip=${skip}&limit=${limit}`);
}

export async function getFinding(id: string): Promise<Finding> {
  return request<Finding>("GET", `/findings/${id}`);
}

export async function createFinding(title: string, description: string): Promise<Finding> {
  return request<Finding>("POST", "/findings", { title, description });
}

export async function updateFinding(
  id: string,
  data: Partial<
    Pick<
      Finding,
      | "title"
      | "description"
      | "verdict"
      | "confidence"
      | "reasoning"
      | "matched_cves"
      | "matched_techniques"
      | "missing_evidence"
      | "recommended_next_steps"
      | "analyst_confirmed"
      | "engagement_id"
      | "affected_scope"
      | "technical_evidence"
      | "reproduction_steps"
      | "impact"
      | "severity"
      | "cvss_score"
      | "cvss_vector"
    >
  >,
): Promise<Finding> {
  return request<Finding>("PATCH", `/findings/${id}`, data);
}

export async function deleteFinding(id: string): Promise<void> {
  return request<void>("DELETE", `/findings/${id}`);
}

// ─── Engagements ──────────────────────────────────────────────────────

export async function listEngagements(): Promise<Engagement[]> {
  return request<Engagement[]>("GET", "/engagements");
}

export async function getEngagement(id: string): Promise<Engagement> {
  return request<Engagement>("GET", `/engagements/${id}`);
}

export async function createEngagement(
  data: Omit<Engagement, "id" | "created_at" | "findings_count">,
): Promise<Engagement> {
  return request<Engagement>("POST", "/engagements", data);
}

// ─── Knowledge Base ───────────────────────────────────────────────────

/**
 * Per-source health. This drives the source badges in the UI.
 *
 * It reads the same health data the retrieval orchestrator routes on, so what
 * the analyst sees and what the RAG actually does cannot disagree.
 */
export async function getSources(refresh = false): Promise<SourcesResponse> {
  return request<SourcesResponse>("GET", `/kb/sources${refresh ? "?refresh=true" : ""}`);
}

export async function reindexSource(
  sourceKey: string,
  force = false,
): Promise<{ source: string; indexed: number; considered: number }> {
  return request("POST", `/kb/sources/${sourceKey}/reindex${force ? "?force=true" : ""}`);
}

export interface KBSearchResponse extends Provenance {
  results: Citation[];
}

/**
 * Federated search. Returns hits *and* the per-source report — callers should
 * render the source status alongside results, since a short result list means
 * something very different when a source was unavailable.
 */
export async function searchKnowledgeBase(
  query: string,
  cvssMin?: number,
  sources?: string[],
  topK?: number,
): Promise<KBSearchResponse> {
  const params = new URLSearchParams();
  params.set("q", query);
  if (cvssMin !== undefined) params.set("cvss_min", String(cvssMin));
  if (sources && sources.length > 0) params.set("sources", sources.join(","));
  if (topK !== undefined) params.set("top_k", String(topK));
  return request<KBSearchResponse>("GET", `/kb/search?${params.toString()}`);
}

export async function listKnowledgeBase(
  source = "nvd",
  skip = 0,
  limit = 50,
): Promise<{ source: string; label: string; entries: KBEntry[] }> {
  const params = new URLSearchParams({
    source,
    skip: String(skip),
    limit: String(limit),
  });
  return request("GET", `/kb/entries?${params.toString()}`);
}

export async function countKnowledgeBase(
  source?: string,
): Promise<{ counts?: Record<string, number>; total?: number; count?: number }> {
  return request("GET", `/kb/entries/count${source ? `?source=${source}` : ""}`);
}

export async function addKnowledgeBaseEntry(
  entry: KBEntryInput,
): Promise<{ id: string; source: string; indexed: number }> {
  return request("POST", "/kb/entries", entry);
}

export async function deleteKnowledgeBaseEntry(source: string, docId: string): Promise<void> {
  return request<void>("DELETE", `/kb/entries/${source}/${encodeURIComponent(docId)}`);
}

// ─── Ghostwriter ──────────────────────────────────────────────────────

export async function listGhostwriterProjects(): Promise<GhostwriterProject[]> {
  return request<GhostwriterProject[]>("GET", "/ghostwriter/projects");
}

export async function pushToGhostwriter(
  findingId: string,
  projectId: string,
): Promise<{ ghostwriter_finding_id: string }> {
  return request<{ ghostwriter_finding_id: string }>("POST", "/ghostwriter/push", {
    finding_id: findingId,
    project_id: projectId,
  });
}

// ─── Reports ──────────────────────────────────────────────────────────

export async function assessReportReadiness(messages: ChatMessage[]): Promise<ReportReadiness> {
  return request<ReportReadiness>("POST", "/reports/readiness", { messages });
}

export async function generateReport(requestData: ReportRequest): Promise<Blob> {
  return request<Blob>("POST", "/reports/generate", requestData);
}

export async function listReports(): Promise<GeneratedReport[]> {
  return request<GeneratedReport[]>("GET", "/reports");
}

export async function listReportTemplates(): Promise<ReportTemplate[]> {
  return request<ReportTemplate[]>("GET", "/reports/templates");
}

export async function uploadReportTemplate(file: File): Promise<ReportTemplate> {
  const form = new FormData();
  form.append("file", file);
  return request<ReportTemplate>("POST", "/reports/templates", form);
}

export async function activateReportTemplate(id: string): Promise<ReportTemplate> {
  return request<ReportTemplate>("POST", `/reports/templates/${id}/activate`);
}

export async function deleteReportTemplate(id: string): Promise<void> {
  return request<void>("DELETE", `/reports/templates/${id}`);
}

export async function downloadReport(id: string): Promise<Blob> {
  return request<Blob>("GET", `/reports/${id}/download`);
}

export async function deleteReport(id: string): Promise<void> {
  return request<void>("DELETE", `/reports/${id}`);
}

// ─── Settings ─────────────────────────────────────────────────────────

export async function getSettings(): Promise<AppSettings> {
  return request<AppSettings>("GET", "/settings");
}

export async function updateSettings(data: Partial<AppSettings>): Promise<AppSettings> {
  return request<AppSettings>("PATCH", "/settings", data);
}

// ─── Audit ────────────────────────────────────────────────────────────

export interface AuditLog {
  id: string;
  event_type: string;
  finding_id: string | null;
  user_id: string | null;
  input_hash: string;
  payload_summary: Record<string, unknown>;
  result_summary: Record<string, unknown>;
  timestamp: string;
}

export async function listAuditLogs(eventType?: string): Promise<AuditLog[]> {
  const params = eventType ? `?event_type=${eventType}` : "";
  return request<AuditLog[]>("GET", `/audit${params}`);
}
