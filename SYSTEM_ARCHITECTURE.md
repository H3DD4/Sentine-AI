# Sentinel.AI System Architecture

## Purpose

Sentinel.AI is an internal red-team finding analysis platform for Forvis Mazars. It combines:

- A React/TanStack Start analyst console.
- A FastAPI backend.
- PostgreSQL as the authoritative relational store.
- Qdrant as the derived vector and sparse-search index.
- Six independently monitored security knowledge sources.
- Pluggable LLM providers for chat, validation, and image understanding.
- Deterministic DOCX report generation.
- Filesystem storage for uploaded evidence, report templates, and generated reports.

The system is designed around an important distinction:

> The LLM may analyze and structure content, but a client report must be generated from stored, analyst-approved data and must not silently invent missing report fields.

This document describes the current implementation in the repository. It also calls out deployment inconsistencies and incomplete production hardening explicitly.

## Reading This Document

There are three kinds of statements in this document:

- **Implemented:** behavior present in the current source code.
- **Configured:** behavior controlled by `.env` or runtime settings.
- **Caveat:** a known mismatch, limitation, or production concern.

The authoritative implementation is in `app/`, `frontend/src/`, `docker-compose.yml`, `nginx.conf`, and the Alembic migrations. Older documentation may describe the former single-table knowledge base and an older report generator; those descriptions are no longer authoritative.

## System Boundary

```text
                         Analyst browser
                              |
                HTTP JSON / multipart / POST SSE
                              |
                 +------------v-------------+
                 | React / TanStack Start   |
                 | frontend/                |
                 +------------+-------------+
                              |
                VITE_API_BASE_URL or same origin
                              |
                 +------------v-------------+
                 | FastAPI application      |
                 | app/main.py               |
                 | Uvicorn / ASGI            |
                 +--+------+------+---------+
                    |      |      |
          +---------+      |      +------------------+
          |                |                         |
  +-------v------+  +------v-------+          +------v-------+
  | PostgreSQL   |  | Qdrant       |          | Filesystem   |
  | source rows, |  | dense/sparse |          | evidence,    |
  | findings,    |  | collections  |          | templates,   |
  | audit,       |  | derived from |          | generated    |
  | reports      |  | PostgreSQL   |          | DOCX reports |
  +--------------+  +--------------+          +--------------+
                    |
             +------v-------------------------------+
             | External services                    |
             | LLM provider, NVD, MITRE, OWASP,     |
             | Ghostwriter GraphQL                   |
             +--------------------------------------+
```

## Components

### Frontend

Location: `frontend/`

The frontend is a React 19 application using:

- TanStack Start.
- TanStack React Router with file-based routes.
- TanStack React Query for server state.
- TypeScript.
- Vite.
- Tailwind CSS.
- Radix UI primitives and local UI components.
- `lucide-react` icons.

The application currently exposes five main routes:

| Route | Source | Responsibility |
|---|---|---|
| `/` | `frontend/src/routes/index.tsx` | Dashboard with findings and engagements. |
| `/chat` | `frontend/src/routes/chat.tsx` | Conversational finding analysis, streaming chat, evidence upload, validation, and readiness assessment. |
| `/knowledge` | `frontend/src/routes/knowledge.tsx` | Source health, source browsing, federated search, and reindex controls. |
| `/report` | `frontend/src/routes/report.tsx` | Finding selection/editing, template selection, section selection, DOCX generation, and report history. |
| `/settings` | `frontend/src/routes/settings.tsx` | Runtime LLM settings and report template upload/activation/deletion. |

`frontend/src/routes/__root.tsx` provides the document shell, global styles, QueryClient provider, error UI, not-found UI, and toast provider.

### Backend

Location: `app/`

The backend is FastAPI with asynchronous SQLAlchemy sessions and an async-capable LLM client. Application assembly and startup are in `app/main.py`.

The backend includes:

- Router layer for HTTP contracts.
- Service layer for retrieval, evidence parsing, validation, readiness, reports, and external APIs.
- Knowledge-source adapter layer.
- Ingestion/indexing layer.
- SQLAlchemy models.
- Pydantic request and response schemas.

### PostgreSQL

PostgreSQL is the authoritative store for:

- User accounts.
- Engagement metadata.
- Analyst findings.
- Evidence metadata and extracted text.
- Audit records.
- Generated report metadata and snapshots.
- Uploaded report-template metadata.
- Source health state.
- Knowledge-source documents.

PostgreSQL rows are the source of truth. Qdrant is derived and can be rebuilt from source rows.

### Qdrant

Qdrant stores derived chunks for retrieval. It is not the authoritative document store.

Each registered knowledge source has its own collection:

```text
kb_nvd
kb_mitre
kb_owasp
kb_owasp_docs
kb_ghostwriter
kb_internal
```

Collections contain:

- Named dense vector: `dense`.
- Named sparse vector: `sparse`.
- Payload containing document identity, source metadata, chunk text, and source-specific filter fields.

When a source is reindexed, PostgreSQL rows are read and Qdrant points are rebuilt or updated. A PostgreSQL row receives `qdrant_synced_at` only after the corresponding vector update succeeds.

### Filesystem Storage

The application stores binary artifacts on the local filesystem:

| Setting | Default | Contents |
|---|---|---|
| `UPLOAD_DIR` | `/tmp/redteam_evidence` | Accepted evidence files. |
| `REPORT_TEMPLATE_DIR` | `/tmp/redteam_report_templates` | Uploaded DOCX templates. |
| `REPORT_DIR` | `/tmp/redteam_reports` | Generated DOCX reports. |

PostgreSQL stores paths and metadata. It does not store the binary report/template/evidence contents directly.

**Operational consequence:** the current Docker Compose file does not mount these three directories as persistent volumes. A container replacement can therefore lose files even when PostgreSQL metadata remains.

## Runtime Startup

Entry point: `run.py`

The checked-in script launches Uvicorn on port `8000`:

```text
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The FastAPI lifespan in `app/main.py` performs these operations:

1. Verify that an Alembic revision exists in the database.
2. Load the dense embedding model.
3. Load the sparse BM25 model.
4. Initialize the Qdrant client.
5. Ensure all registered Qdrant collections exist.
6. Check the health of every registered knowledge source.
7. Create upload, report, and template directories.
8. Start APScheduler jobs.
9. Serve requests.
10. Stop the scheduler during shutdown.

Startup logs schema problems but does not currently fail the process when the schema is missing or behind the migration head. Deployments must run:

```text
alembic upgrade head
```

before starting the application.

## Router and API Map

### Authentication: `/auth`

Source: `app/routers/auth.py`

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/auth/register` | Create a user account. |
| `POST` | `/auth/login` | Issue a JWT token. |

### Chat: `/chat`

Source: `app/routers/chat.py`

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/chat` | JSON chat, validation action, or one-finding report action. |
| `POST` | `/chat/with-evidence` | Multipart conversation with evidence. Supports validation and non-validation analysis. |
| `POST` | `/chat/stream` | POST-based SSE conversational chat. |

`POST /chat` multiplexes behavior using `action`:

- No action: retrieve context and generate a normal assistant response.
- `action="validate"`: validate the user conversation and persist a finding.
- `action="generate_report"`: generate a direct one-finding DOCX response. This legacy action is separate from the `/reports/generate` report-builder workflow and does not provide the full template/section/history behavior.

### Validation: `/validate`

Source: `app/routers/validate.py`

| Method | Path | Body | Purpose |
|---|---|---|---|
| `POST` | `/validate` | Multipart `title`, `description`, and zero or more `files`. | Parse evidence, retrieve knowledge, validate with an LLM, persist finding/evidence/audit, and return verdict plus provenance and processing manifest. |

### Findings: `/findings`

Source: `app/routers/findings.py`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/findings` | Paginated finding list with optional search. |
| `POST` | `/findings` | Create a manually authored finding. |
| `GET` | `/findings/{id}` | Load one finding with evidence. |
| `PATCH` | `/findings/{id}` | Update analyst-owned finding fields. |
| `DELETE` | `/findings/{id}` | Delete finding, evidence rows, and evidence files. |

Structured report fields on a finding include:

- `title`.
- `description`.
- `affected_scope`.
- `technical_evidence`.
- `reproduction_steps`.
- `impact`.
- `severity`.
- `cvss_score`.
- `cvss_vector`.
- `verdict`.
- `confidence`.
- `reasoning`.
- Matched CVEs and MITRE techniques.
- Missing evidence and recommended next steps.

### Knowledge Base: `/kb`

Source: `app/routers/kb.py`

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/kb/sources` | Show health and coverage for all six sources. |
| `POST` | `/kb/sources/{source_key}/reindex` | Rebuild vectors for one source. |
| `GET` | `/kb/search` | Federated search with provenance. |
| `GET` | `/kb/entries` | Browse one source. |
| `GET` | `/kb/entries/count` | Count one or all sources. |
| `POST` | `/kb/entries` | Create an internal knowledge document. |
| `DELETE` | `/kb/entries/{source}/{doc_id}` | Delete a source document and its vectors. |

Omitting `sources` from `/kb/search` means all registered sources are searched.

### Reports: `/reports`

Source: `app/routers/report.py`

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/reports/readiness` | Extract and score a conversation-derived report draft. |
| `POST` | `/reports/generate` | Validate content, generate deterministic DOCX, persist report history, and return DOCX bytes. |
| `GET` | `/reports` | List generated report history. |
| `GET` | `/reports/{id}/download` | Download a stored generated report. |
| `DELETE` | `/reports/{id}` | Delete generated report metadata and file. |
| `GET` | `/reports/templates` | List uploaded templates. |
| `POST` | `/reports/templates` | Upload a DOCX template. |
| `POST` | `/reports/templates/{id}/activate` | Select the active template. |
| `DELETE` | `/reports/templates/{id}` | Delete a template. |

### Other Routers

| Prefix | Purpose |
|---|---|
| `/engagements` | Engagement CRUD. |
| `/ghostwriter` | Ghostwriter project lookup and finding push. |
| `/settings` | Process-local runtime LLM settings. |
| `/audit` | Audit log listing. |

## PostgreSQL Data Model

The ORM models are in `app/models.py` and `app/kb/models.py`. Alembic owns schema changes.

### Application Tables

#### `users`

Stores email, password hash, active flag, and creation time.

JWT utilities exist, but current routers do not consistently require the authenticated-user dependency. Authentication is therefore implemented as an API capability, not as an enforced application boundary.

#### `engagements`

Stores:

- Client name.
- Unique engagement code.
- Scope.
- Lead.
- Status.
- Progress.
- Findings count.
- Start/end dates.

The progress and findings-count fields are stored values. They are not automatically recalculated by every finding mutation.

#### `findings`

Stores analyst findings and the result of validation. It is the central entity for report generation.

Important relationships:

```text
Engagement 1 ---- many Finding
Finding    1 ---- many Evidence
```

#### `evidence`

Stores metadata for a finding-owned artifact:

- Original sanitized filename.
- File type.
- Filesystem path.
- Extracted text when available.
- Image description when available.
- Upload timestamp.

The complete binary artifact is stored at `storage_path`; the extracted text is an analysis representation, not necessarily the complete file contents.

#### `audit_logs`

Stores an event type, optional finding/user identifiers, an input hash, payload summary, result summary, and timestamp.

Validation audit payloads include processing manifests and source provenance. Report audit payloads include selected findings, template ID, and selected report sections.

#### `generated_reports`

Stores:

- Report ID.
- Client name.
- Engagement title.
- Download filename.
- Filesystem storage path.
- Finding snapshot.
- Optional conversation draft snapshot.
- Creation time.

#### `report_templates`

Stores:

- Template ID.
- Original template name.
- Filesystem storage path.
- File size.
- Active flag.
- Creation time.

Only one template is intended to be active at a time. The first uploaded template becomes active automatically; activation clears the flag on the other templates.

### Knowledge Tables

The knowledge base deliberately uses one physical PostgreSQL table per source. This avoids forcing incompatible schemas into a legacy `kb_entries` table and gives each source its own failure and indexing state.

| Source key | PostgreSQL table | Qdrant collection | Content |
|---|---|---|---|
| `nvd` | `nvd_entries` | `kb_nvd` | NVD CVEs and CVSS/CWE/product data. |
| `mitre` | `mitre_techniques` | `kb_mitre` | ATT&CK techniques and sub-techniques. |
| `owasp` | `owasp_top10` | `kb_owasp` | OWASP Top 10 categories. |
| `owasp_docs` | `owasp_documents` | `kb_owasp_docs` | Official OWASP guide documents. |
| `ghostwriter` | `ghostwriter_findings` | `kb_ghostwriter` | Historical internal findings from Ghostwriter. |
| `internal` | `internal_docs` | `kb_internal` | Analyst-authored internal notes and checklists. |

Every knowledge table uses the sync fields:

- `content_hash`: hash of the exact text used for embedding.
- `embed_model`: model signature used to index the row.
- `qdrant_synced_at`: written after successful vector indexing.
- Creation/update timestamps.

`kb_source_state` stores operational status, enabled state, PostgreSQL row count, Qdrant vector count, unsynced count, last checks, and errors.

## Knowledge Source Adapters

The adapter contract is defined by `app/kb/base.py`. The retrieval orchestrator operates on the common `KBSource` interface rather than source-specific models.

Each adapter defines:

- Stable source key and display name.
- Backing SQLAlchemy model.
- Qdrant collection derived as `kb_<source_key>`.
- Primary-key extraction.
- Text construction for embedding.
- Payload construction for citations and filters.
- Payload-to-`RetrievalHit` normalization.
- Exact identifier extraction.
- Source-specific fusion weight.

Current source weights are:

| Source | Weight |
|---|---:|
| NVD | 1.0 |
| MITRE | 1.0 |
| OWASP Top 10 | 1.1 |
| OWASP documents | 1.15 |
| Ghostwriter | 1.25 |
| Internal | 1.1 |

These weights affect cross-source rank fusion. They are not probability scores and must not be interpreted as confidence.

## Source Health and Degradation

Health is checked per source and cached in-process for 30 seconds.

The health checker compares:

- PostgreSQL row count.
- Qdrant collection existence.
- Qdrant point count.
- PostgreSQL rows whose `qdrant_synced_at` is null.
- Operator-enabled state.

Possible states:

| State | Meaning | Retrieval behavior |
|---|---|---|
| `ok` | Source is reachable and indexed. | Search normally. |
| `no_match` | Search succeeded but no document matched. | Do not treat as outage. |
| `empty` | Source has no searchable data. | Skip and report. |
| `degraded` | PostgreSQL/Qdrant coverage may be partial. | Search, but disclose partial coverage. |
| `unavailable` | Source or backend probe failed. | Skip and disclose failure. |
| `disabled` | Operator disabled source. | Skip and disclose disabled state. |

Every retrieval response includes:

- `sources`: per-source reports.
- `sources_used`: sources that contributed final hits.
- `degraded`: whether a source/ranking failure occurred.
- `provenance`: analyst-facing coverage statement.
- `citations` or `results`.

The same provenance is injected into LLM prompts and rendered in the frontend. This prevents an answer from appearing fully grounded when a source was unavailable.

## Qdrant Indexing Architecture

Document indexing is handled by `app/kb/indexer.py` and embedding helpers in `app/ingestion/embedder.py`.

### Chunking

Current indexing uses tokenizer-aware chunks with approximately:

- 480 tokens per chunk.
- 64-token overlap.

Each chunk has a deterministic point ID derived from document ID and chunk index. Reindexing the same row therefore produces stable point identity.

### Dense Vector

Configured model:

```text
BAAI/bge-base-en-v1.5
```

The default dense vector size is 768 dimensions. Document text is embedded without the query instruction prefix; query embedding uses the BGE retrieval prefix.

### Sparse Vector

Configured sparse model:

```text
Qdrant/bm25
```

Sparse retrieval is useful for exact identifiers, versions, product strings, CPEs, CWEs, and literal security terminology.

### Incremental Reindexing

Rows with unchanged content hash and embedding configuration can be skipped. A successful vector upsert stamps the row as synchronized. If a document shrinks, stale chunks are removed.

PostgreSQL is committed before vector indexing. This means an indexing failure can leave a row present with `qdrant_synced_at` null; health then exposes the drift.

## Federated Retrieval

The main implementation is `app/services/retrieval.py`.

### Single Query Flow

```text
Input query
    |
    +--> Extract exact IDs per source
    |
    +--> Dense query embedding
    |
    +--> Sparse BM25 query embedding
    |
    +--> Read cached source health
    |
    +--> Fan out to every selected healthy source
              |
              +--> dense prefetch
              +--> sparse prefetch
              +--> Qdrant server-side RRF
              +--> exact-ID payload lookup
              +--> best chunk per document
              +--> max 15 documents per source
    |
    +--> Weighted cross-source RRF, K=60
    |
    +--> Optional cross-encoder reranking
    |
    +--> Top 8 final hits by default
    |
    +--> Source reports + citations + provenance
```

### Retrieval Constants

| Constant | Value | Meaning |
|---|---:|---|
| `PREFETCH_LIMIT` | 40 | Dense/sparse candidates per arm. |
| `PER_SOURCE_LIMIT` | 15 | Documents retained per source before cross-source fusion. |
| `RERANK_CANDIDATES` | 40 | Candidates passed to the cross-encoder. |
| `FINAL_TOP_K` | 8 | Default final hits. |
| `RRF_K` | 60 | Reciprocal-rank damping constant. |
| `RERANK_MIN_SCORE` | -2.0 | Cross-encoder score floor. |

### Exact Identifier Retrieval

Identifiers are extracted from query text and looked up directly in the source collection. Examples:

- `CVE-2024-...` for NVD.
- `T1059.001` for MITRE.
- OWASP category identifiers for OWASP.

Exact-ID hits are pinned ahead of semantic neighbors. The same source filters used by the hybrid search arm are applied to exact-ID lookups, including MITRE deprecation filtering and payload filters.

### Filters

- `cvss_min` applies to NVD and Ghostwriter fields where supported.
- MITRE semantic and exact-ID retrieval excludes deprecated techniques by default.
- Additional payload filters are combined with source filters.

### Reranking Degradation

If the cross-encoder is not cached or cannot load while model downloads are disabled, retrieval keeps weighted-RRF ordering and adds a retrieval note. Retrieval does not fail solely because reranking is unavailable.

## Multimodal Retrieval and Evidence Context

Multimodal retrieval is implemented in `app/services/query_planner.py` and `multimodal_search()`.

The system intentionally does not embed the description and a large log dump as one centroid query. It creates focused query arms:

| Arm | Weight | Purpose |
|---|---:|---|
| Description | 1.0 | Analyst's actual question. |
| Identifier | 1.2 | Exact CVE, ATT&CK, or OWASP identifiers. |
| Evidence | 0.7 | Log/tool output and technical strings. |
| Image | 0.55 | OCR/vision-derived evidence. |

At most five query arms are issued. Evidence and image inputs are selected across the full file sequence rather than always taking only the first files. Near-duplicate query arms are removed before querying.

## Evidence Upload Pipeline

Evidence upload processing is shared by `/validate` and `/chat/with-evidence` through `app/services/upload_processing.py`.

### Staging Flow

```text
Multipart UploadFile
    |
    +--> sanitize basename
    |
    +--> stream in configured chunks to temporary staged path
    |
    +--> enforce file count, per-file bytes, aggregate bytes
    |
    +--> parse bounded bytes from staged artifact
    |
    +--> produce analysis representation and processing manifest
    |
    +--> validation success: move staged artifact to finding path
    |
    +--> conversation-only or failure: delete staged artifact
```

### Configurable Upload Limits

| Setting | Default | Purpose |
|---|---:|---|
| `EVIDENCE_MAX_FILES` | 20 | Maximum files per multipart request. |
| `EVIDENCE_MAX_FILE_BYTES` | 50 MB | Maximum individual uploaded artifact. |
| `EVIDENCE_MAX_TOTAL_BYTES` | 100 MB | Maximum aggregate request artifact bytes. |
| `EVIDENCE_UPLOAD_CHUNK_BYTES` | 1 MB | Streaming write chunk size. |

These are infrastructure safety limits, not UI text limits. The complete accepted binary artifact is retained on disk, while bounded excerpts are used for extraction, retrieval, and model context.

### File Types

- Text/log: `.txt`, `.log`, `.csv`, `.xml`, `.json`, `.yaml`, `.yml`, `.md`.
- Images: `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp`.
- PDF: `.pdf`.
- Other files: retained as binary and marked for manual review.

### Processing Manifest

Each processed file reports:

- Filename.
- File type.
- Stored bytes.
- Extracted characters.
- Selected characters used for analysis.
- Manual-review status.
- Notices explaining extraction or context omission.

The manifest is returned to the frontend and included in validation audit payloads.

### Text and PDF Extraction

Text and PDF parsers use bounded extraction limits:

- Text parser limit: 10 MB of parser input.
- PDF parser limit: 50 MB of parser input.
- LLM analysis selection: shared 32,000-character evidence budget across text files.

For oversized extracted content, beginning and ending regions are retained with an explicit omission marker. The middle is not silently discarded from the stored binary artifact.

### Image Processing

Images below the vision limit are sent to the configured vision-capable LLM. Images above the limit are retained but marked for manual review. The image description is used as retrieval and validation context; the original image remains the stored artifact.

## Validation Pipeline

The dedicated validation flow is:

```text
POST /validate
    |
    +--> Stream and stage files
    +--> Parse evidence
    +--> Build bounded evidence/image analysis inputs
    +--> multimodal_search(description, evidence, images)
    +--> Build source-labelled KB context
    +--> Add DATA COVERAGE / provenance block
    +--> Add bounded finding/evidence/image context
    +--> AsyncLLMClient.generate_validation()
    +--> Parse strict ValidationResult JSON
    +--> Cap confidence at 0.7 when retrieval is degraded
    +--> Persist Finding
    +--> Move staged artifacts and persist Evidence rows
    +--> Persist AuditLog with provenance and processing manifest
    +--> Return verdict, citations, provenance, processing manifest
```

### Validation Prompt Budgets

The validation service applies separate budgets:

| Context | Budget |
|---|---:|
| Finding description | 24,000 characters |
| Text/log evidence | 24,000 characters total |
| Image descriptions | 8,000 characters total |
| Retrieved KB context | 16,000 characters |

Beginning and ending excerpts are used when a budget is exceeded, and the retrieval outcome records a notice.

### Validation Output

The LLM must return:

- Verdict: `confirmed`, `likely`, `insufficient`, or `false_positive`.
- Confidence.
- Reasoning.
- Matched CVEs.
- Matched MITRE techniques.
- Missing evidence.
- Evidence-focused next steps.

The validation result is not itself a final client report. It becomes input to analyst review and report-builder editing.

## Conversational Chat Pipeline

Text-only chat uses `POST /chat/stream`.

```text
Browser sends conversation JSON
    |
    +--> Choose latest user turn as retrieval query
    +--> Keep only a bounded recent provider history
    +--> Federated retrieval with timeout
    +--> Build source-labelled prompt context
    +--> Emit SSE provenance frame
    +--> Emit token frames from AsyncLLMClient
    +--> Emit done frame
```

The backend starts retrieval inside the event generator so the response stream is not considered active before retrieval actually begins. The frontend uses `fetch()` and `ReadableStream` rather than `EventSource` because the endpoint is a POST request with JSON input.

SSE frames are:

```text
data: {"sources": [...], "provenance": "...", "degraded": false, ...}

data: {"token": "..."}

data: {"done": true}
```

If the LLM fails, the backend emits an error frame followed by `done`.

### Conversation Persistence

The chat transcript is stored in browser `sessionStorage` under:

```text
sentinel.chat.v1
```

It is restored after hydration to avoid server/client markup divergence. Attached binary files are not persisted in browser storage; only filenames are part of the message display.

## Report Readiness

`POST /reports/readiness` accepts normalized conversation messages.

The readiness service:

1. Avoids an LLM call for social-only greetings.
2. Sends the bounded conversation to structured extraction.
3. Normalizes malformed fields conservatively.
4. Scores report dimensions deterministically.
5. Returns a `ReportDraft` and missing sections.
6. Falls back to analyst text when the LLM is unavailable.

The readiness threshold is advisory and currently set to `3.5/10`. The report builder separately checks exact export requirements.

The handoff from chat to report builder is stored in:

```text
sentinel.report.handoff.v1
```

It contains:

- Readiness response.
- Normalized conversation messages.
- Optional finding ID.

## Report Generation Architecture

The report builder is the client-deliverable boundary.

### Report Generation Request

The frontend sends:

```json
{
  "finding_ids": ["finding-id"],
  "engagement_title": "External penetration test",
  "client_name": "Client name",
  "draft": null,
  "template_id": "template-id-or-null",
  "sections": [
    "executive_summary",
    "scope_methodology",
    "findings_overview",
    "detailed_findings",
    "attack_mapping",
    "evidence_gaps",
    "disclaimer"
  ]
}
```

### Server-Side Export Gate

For every selected finding or conversation draft, export requires:

- Title.
- Description.
- Affected scope.
- Technical evidence.
- Impact.
- Severity.
- Analyst verdict.

If any required field is missing, `/reports/generate` returns HTTP `422` with the finding title and missing fields. It does not produce a report containing `Pending`, `Not recorded`, or empty client-facing sections.

### Template Behavior

Templates are uploaded DOCX files. The service:

1. Validates the extension.
2. Streams the file to `REPORT_TEMPLATE_DIR`.
3. Validates it with `python-docx`.
4. Records it in `report_templates`.
5. Uses the first template as active by default.
6. Allows activation/deletion through the settings UI.

When a template is selected, it is loaded as the base document. Supported placeholders are:

```text
{{CLIENT_NAME}}
{{ENGAGEMENT_TITLE}}
{{GENERATED_AT}}
{{TOTAL_FINDINGS}}
```

The implementation replaces placeholders in normal paragraphs, table cells, headers, and footers. It also handles placeholders split across Word runs by replacing the paragraph text while preserving the paragraph location.

The generated report sections are deterministic and are appended after the template content. The exporter does not call an LLM.

### Selectable Sections

The frontend and backend support:

- `executive_summary`.
- `scope_methodology`.
- `findings_overview`.
- `detailed_findings`.
- `attack_mapping`.
- `evidence_gaps`.
- `disclaimer`.

The selected section list is saved in the report audit payload.

### Report History

After successful generation:

1. DOCX bytes are written under `REPORT_DIR`.
2. A `GeneratedReport` row is created.
3. A report-generation `AuditLog` row records selected findings, template, sections, and output hash.
4. The response returns DOCX bytes and an `X-Report-ID` header.

## LLM Architecture

Implementation: `app/services/llm_client.py`

Supported providers:

- Anthropic.
- OpenAI.
- Together AI.
- OpenRouter.
- Ollama.
- Gemini.

The active provider is selected from configuration and can be overridden process-locally through `/settings`.

The client supports three roles:

- Chat model.
- Validation model.
- Vision model.

LLM calls use asynchronous SDKs where available. Provider calls use retry behavior with exponential delay. Structured validation attempts JSON extraction and retries malformed responses.

Default model configuration comes from `app/config.py`; `.env` may override it. The deployed runtime should be treated as the source of truth for the active provider/model because the repository may contain different example defaults.

Model downloads are disabled by default with `ALLOW_MODEL_DOWNLOADS=False`. Serving therefore expects embedding and reranker models to be warmed or cached ahead of time.

## Ingestion and Synchronization

### NVD

Source: `app/ingestion/nvd_sync.py`

- Uses the NVD API.
- Fetches recently modified CVEs using a rolling window.
- Paginates upstream results.
- Persists NVD rows.
- Indexes rows into `kb_nvd`.
- Uses API-key-dependent throttling.

Scheduled every six hours by APScheduler.

### MITRE ATT&CK

Source: `app/ingestion/mitre_sync.py`

- Downloads the official enterprise ATT&CK STIX bundle.
- Parses techniques and sub-techniques.
- Records ATT&CK release/version provenance.
- Preserves deprecated/revoked records for historical discoverability while filtering them from normal semantic retrieval.
- Removes stale records only after successful indexing of the current source.

Scheduled every seven days.

### OWASP

Sources:

- `app/ingestion/owasp_sync.py` for OWASP Top 10.
- `app/ingestion/owasp_docs_sync.py` for broader official OWASP documents.

OWASP ingestion is run manually through:

```text
python -m scripts.sync_security_kb
```

The broader corpus includes official English documents from projects such as the Cheat Sheet Series, WSTG, ASVS, and API Security.

### Internal Knowledge

Internal documents are authored through `POST /kb/entries`. They are persisted in `internal_docs` and immediately indexed into `kb_internal`.

### Ghostwriter

Ghostwriter is an external source and integration. The adapter can search synchronized `ghostwriter_findings`; the current repository does not contain a scheduled synchronization job that populates that table.

## Frontend State Architecture

### Server State

TanStack Query caches:

- Findings.
- Engagements.
- Knowledge-source health.
- Knowledge entries.
- Knowledge search responses.
- Report history.
- Report templates.
- Runtime settings.

Mutations invalidate the relevant query keys after changes.

### Local Workflow State

Chat and report-builder forms use React component state. Active stream cleanup functions are stored in refs. The frontend does not use Redux or Zustand.

### API Client

`frontend/src/lib/api.ts` contains one typed fetch wrapper. It:

- Uses `VITE_API_BASE_URL` when configured.
- Uses same-origin requests in deployed same-origin mode.
- Defaults development browser requests to `http://localhost:8000` when running on another non-empty port.
- Sends JSON or `FormData`.
- Parses FastAPI `detail` errors.
- Supports DOCX Blob responses.
- Implements manual POST-SSE parsing and abort behavior.

## Deployment Architecture

### Docker Compose

The current `docker-compose.yml` starts only infrastructure:

| Service | Image | Host ports | Persistent volume |
|---|---|---|---|
| PostgreSQL | `postgres:15-alpine` | `5544 -> 5432` | `postgres_data` |
| Qdrant | `qdrant/qdrant:latest` | `6333`, `6334` | `qdrant_data` |

Compose does not start FastAPI, the frontend, Nginx, migrations, or a separate worker.

### Nginx

`nginx.conf` is intended to:

- Listen on port 80.
- Proxy to a backend at `host.docker.internal:8003`.
- Apply request rate and connection limits.
- Allow 100 MB request bodies.
- Disable request buffering for uploads.
- Proxy SSE with HTTP/1.1 and long read timeout.
- Add security response headers.

Known mismatches:

- `run.py` launches FastAPI on port `8000`, while Nginx expects `8003`.
- Nginx has a `/static/` location while FastAPI and TanStack output use `/assets/`.
- Nginx API regexes with a trailing slash do not match exact paths such as `/chat` and `/reports`.
- The local Nginx `/health` response checks only Nginx, not FastAPI or dependencies.
- TLS redirect is commented out.

### Frontend Build

`npm run build` produces TanStack Start/Nitro output under `frontend/.output` using the current wrapper configuration. FastAPI's static fallback expects `frontend/dist` instead.

This creates two possible deployment models that are not currently unified:

1. Deploy TanStack Start/Nitro output independently.
2. Build a frontend artifact in a format FastAPI can serve from `frontend/dist`.

The repository does not currently provide a Dockerfile or a combined application container.

## Configuration

Primary configuration is in `app/config.py` and `.env`.

Important settings:

| Setting | Responsibility |
|---|---|
| `DATABASE_URL` | Async PostgreSQL connection. |
| `QDRANT_URL` | Qdrant HTTP endpoint. |
| `EMBEDDING_MODEL` | Dense embedding model. |
| `RERANKER_MODEL` | Cross-encoder reranker. |
| `RERANK_ENABLED` | Enable/disable reranking. |
| `ALLOW_MODEL_DOWNLOADS` | Permit Hugging Face downloads. |
| `LLM_PROVIDER` | Default LLM provider. |
| `*_CHAT_MODEL` | Provider-specific chat model. |
| `*_VALIDATION_MODEL` | Provider-specific validation model. |
| `*_VISION_MODEL` | Provider-specific vision model. |
| `LLM_REQUEST_TIMEOUT_SECONDS` | Provider call timeout. |
| `NVD_API_KEY` | NVD API access/throttling. |
| `MITRE_STIX_URL` | Official ATT&CK bundle. |
| `GHOSTWRITER_URL` | Optional external Ghostwriter server. |
| `GHOSTWRITER_API_KEY` | Ghostwriter credential. |
| `UPLOAD_DIR` | Evidence files. |
| `REPORT_DIR` | Generated report files. |
| `REPORT_TEMPLATE_DIR` | Uploaded DOCX templates. |
| `EVIDENCE_MAX_FILES` | Evidence file count limit. |
| `EVIDENCE_MAX_FILE_BYTES` | Per-file evidence limit. |
| `EVIDENCE_MAX_TOTAL_BYTES` | Aggregate evidence limit. |
| `EVIDENCE_UPLOAD_CHUNK_BYTES` | Streaming upload chunk size. |
| `CORS_ORIGINS` | Allowed browser origins. |

Runtime settings changed through `/settings` are held in an in-memory dictionary. They disappear on restart and are not shared between multiple backend workers.

## Security Model and Current Gaps

### Implemented Controls

- Upload filenames are reduced to safe basenames.
- Uploads stream to disk and enforce file count/size bounds.
- Oversized artifacts are retained only within configured infrastructure limits and are marked for review when not processable.
- Report export rejects incomplete finding content.
- Evidence, reports, and templates use separate filesystem paths.
- Audit logs record validation and report-generation provenance.
- Qdrant collections are source-separated.
- Retrieval reports source availability rather than hiding failures.
- Model downloads are disabled by default during serving.
- Nginx configuration includes security headers and rate-limit zones.

### Current Gaps

- Authentication endpoints exist, but authentication is not consistently enforced by routers or integrated into the frontend API client.
- No frontend token storage or Authorization header is currently used.
- `API_KEY` is configured but not a general request-authentication boundary.
- SlowAPI is initialized, but endpoint decorators are not broadly applied.
- Runtime settings are process-local.
- Filesystem artifact directories are not mounted in the current Compose file.
- Default/example secrets and environment values require deployment-specific replacement.
- Nginx and backend port/output assumptions are inconsistent.
- Audit listing is currently unpaginated.
- There is no dedicated worker process; scheduled jobs run inside the FastAPI process.

## Failure and Recovery Behavior

### Knowledge Source Failure

One unavailable source is skipped. Other healthy sources still answer. The response includes the unavailable source and reason.

### Dense Model Failure

Startup logs the failure, but the process continues. Retrieval that requires dense vectors may fail or return a degraded/unavailable outcome depending on the call path.

### Sparse Model Failure

Indexing/search can fall back to dense-only behavior and add a retrieval note explaining that sparse/exact matching was unavailable.

### Reranker Failure

Weighted RRF order is retained. A retrieval note states that the cross-encoder was unavailable.

### Chat Retrieval Timeout

Normal chat retrieval is bounded by a timeout. Chat can continue with an explicitly ungrounded/degraded outcome rather than silently claiming full knowledge coverage.

### LLM Quota or Provider Failure

- Chat returns an error/degraded response according to endpoint behavior.
- Validation generally returns an upstream failure rather than fabricating a verdict.
- Readiness uses a conservative extraction fallback.
- Streaming chat emits an SSE error frame and completion frame.

### Qdrant/PostgreSQL Drift

Rows without confirmed vector synchronization are exposed by source health. Reindexing a source rebuilds vectors from PostgreSQL rows.

### Failed Evidence Processing

Staged files are cleaned up when parsing, retrieval, validation, persistence, or LLM processing fails. Successful validation moves artifacts from staging to finding-specific paths.

### Report Generation Failure

The DOCX is written only after content validation and generation succeed. Failed incomplete reports return HTTP `422`; missing templates return `404` or `410` depending on whether metadata or the underlying file is missing.

## Operational Commands

### Infrastructure

```text
docker compose up -d postgres qdrant
alembic upgrade head
```

### Backend

```text
python run.py
```

The checked-in `run.py` uses port `8000`.

### Frontend Development

```text
cd frontend
npm install
npm run dev
```

Set `VITE_API_BASE_URL` if the backend is not at the frontend's default development target.

### Frontend Production Build

```text
cd frontend
npm run build
```

Verify whether the deployment target expects TanStack/Nitro `.output` or FastAPI-served `frontend/dist`; the current repository does not automatically reconcile those output models.

### Security Knowledge Synchronization

```text
python -m scripts.sync_security_kb
```

This handles OWASP and MITRE-oriented synchronization/reporting according to the script's current options.

## File Map

```text
Mazars_rag/
├── run.py                         FastAPI/Uvicorn launcher
├── requirements.txt               Backend dependencies
├── docker-compose.yml             PostgreSQL and Qdrant only
├── nginx.conf                     Reverse proxy configuration
├── alembic.ini                    Alembic configuration
├── .env.example                   Environment template
├── SYSTEM_ARCHITECTURE.md         This document
├── app/
│   ├── main.py                    FastAPI assembly and lifespan
│   ├── config.py                  Pydantic settings
│   ├── db.py                      Async SQLAlchemy engine/session
│   ├── models.py                  Application ORM models
│   ├── schemas.py                 Pydantic API contracts
│   ├── auth.py                    JWT/password utilities
│   ├── routers/
│   │   ├── chat.py                Chat, SSE, multipart evidence
│   │   ├── validate.py             Dedicated validation upload endpoint
│   │   ├── findings.py             Finding CRUD
│   │   ├── kb.py                   Knowledge source/search management
│   │   ├── report.py               Readiness, templates, report generation/history
│   │   ├── engagements.py          Engagement CRUD
│   │   ├── ghostwriter.py          Ghostwriter integration
│   │   ├── settings.py             Runtime provider/model settings
│   │   ├── auth.py                 Registration/login
│   │   └── audit.py                Audit listing
│   ├── services/
│   │   ├── retrieval.py            Federated dense/sparse/exact/RRF retrieval
│   │   ├── query_planner.py         Multimodal query planning/fusion
│   │   ├── validation.py             RAG validation prompt and result handling
│   │   ├── evidence.py               Text/PDF/image extraction
│   │   ├── upload_processing.py     Streaming staging and processing manifests
│   │   ├── llm_client.py             Multi-provider async LLM client
│   │   ├── report.py                 Deterministic DOCX generation
│   │   ├── report_readiness.py       Draft extraction and deterministic scoring
│   │   └── ghostwriter_client.py     External Ghostwriter GraphQL client
│   ├── kb/
│   │   ├── base.py                  Source adapter contracts and provenance types
│   │   ├── models.py                Source-specific ORM tables
│   │   ├── registry.py              Authoritative six-source registry/health
│   │   ├── indexer.py               PostgreSQL-to-Qdrant indexing
│   │   └── sources/                  NVD, MITRE, OWASP, Ghostwriter, internal adapters
│   └── ingestion/
│       ├── embedder.py              Dense/sparse model loading and embeddings
│       ├── nvd_sync.py               NVD synchronization
│       ├── mitre_sync.py             ATT&CK synchronization
│       ├── owasp_sync.py             OWASP Top 10 synchronization
│       └── owasp_docs_sync.py        Official OWASP document synchronization
├── scripts/
│   ├── sync_security_kb.py           Manual security KB sync entry point
│   └── warm_models.py                Deliberate model warm-up
├── alembic/versions/                 PostgreSQL migrations
├── tests/                             Backend regression tests
└── frontend/
    ├── package.json                  Frontend scripts/dependencies
    ├── vite.config.ts                TanStack/Vite/Nitro configuration
    └── src/
        ├── lib/api.ts                Typed fetch client and contracts
        ├── router.tsx                 QueryClient/router creation
        ├── routes/                    Dashboard, chat, knowledge, report, settings
        ├── components/                Layout, brand, and Radix-based UI
        └── styles.css                 Global styling
```

## Known Architecture Decisions to Revisit

These are not hidden defects; they are explicit next architectural decisions:

1. Choose one canonical deployment model: FastAPI-served frontend or independent TanStack/Nitro deployment.
2. Align backend, Nginx, frontend API, and CORS ports.
3. Add persistent volumes or object storage for evidence, templates, and reports.
4. Enforce authentication consistently across all routers and integrate it into the frontend.
5. Persist runtime settings in a database or configuration service if multiple workers are used.
6. Move scheduled ingestion out of the web process if synchronization duration or failure isolation requires it.
7. Add a real Ghostwriter synchronization job if historical findings are expected to be a live retrieval source.
8. Add a dedicated production health/readiness endpoint that checks database, Qdrant, models, and source availability separately.
9. Keep the legacy `/chat` report action separate or remove it once `/reports/generate` is the sole report contract.
10. Add integration tests for template upload, activation, placeholder replacement, incomplete export rejection, and artifact persistence in the deployed environment.
