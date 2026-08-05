# Sentinel.AI — Full System Architecture

> **Forvis Mazars Red Team RAG** — AI-powered penetration testing finding validation and reporting platform.
> Project path: `c:\Users\MSI\OneDrive\Bureau\Mazars_rag`

---

## 1. High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER / BROWSER                              │
│                    (React SPA on port 3000)                          │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ HTTP / SSE
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        NGINX REVERSE PROXY (port 80)                 │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Rate limiting (30r/s) · Security headers · TLS termination   │  │
│  │  Static file caching · 100 MB upload limit                    │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ proxy_pass
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     FASTAPI BACKEND (uvicorn :8003)                   │
│                                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │ Auth     │  │ Chat     │  │ Validate │  │ Findings │  ...routers  │
│  │ (/auth)  │  │ (/chat)  │  │(/validate)│  │(/findings)│            │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘             │
│       │              │             │              │                    │
│       │         ┌────▼────┐       │              │                    │
│       │         │ Hybrid  │       │              │                    │
│       │         │ Search  │       │              │                    │
│       │         └────┬────┘       │              │                    │
│       │              │            │              │                    │
│       ▼              ▼            ▼              ▼                    │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                     SERVICES LAYER                               │  │
│  │  ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌───────────────┐   │  │
│  │  │ LLM Client │ │Validation  │ │ Report   │ │ Ghostwriter   │   │  │
│  │  │(AsyncLLM)  │ │(RAG+LLM)   │ │(DOCX)    │ │ Client (GQL)  │   │  │
│  │  └─────┬──────┘ └─────┬──────┘ └────┬─────┘ └──────┬────────┘   │  │
│  │  ┌─────▼──────┐ ┌─────▼──────┐      │               │           │  │
│  │  │ Evidence   │ │ Retrieval  │      │               │           │  │
│  │  │ Parser     │ │ (Qdrant+)  │      │               │           │  │
│  │  └────────────┘ └────────────┘      │               │           │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                      │                   │              │              │
│         ┌────────────┘                   │              │              │
│         ▼                                ▼              ▼              │
│  ┌──────────────┐               ┌────────────────┐ ┌──────────────┐   │
│  │  PostgreSQL  │               │    Qdrant      │ │ Ghostwriter  │   │
│  │  (port 5432) │               │  (port 6333)   │ │ (External)   │   │
│  └──────────────┘               └────────────────┘ └──────────────┘   │
│         ▲                                ▲                             │
│         │                                │                             │
│  ┌──────┴──────┐               ┌────────┴────────┐                    │
│  │  Alembic    │               │ NVD Sync (6h)   │                    │
│  │  Migrations │               │ MITRE Sync (7d) │                    │
│  └─────────────┘               └─────────────────┘                    │
└──────────────────────────────────────────────────────────────────────┘
                           │
                           ▼
            ┌─────────────────────────────────────────────┐
            │  LLM Provider: OpenRouter (free models)     │
            │  • Chat/Validation: mistral-7b-instruct:free│
            │  • Vision: phi-3-vision-128k-instrucls
            t:free  │
            └─────────────────────────────────────────────┘
```

---

## 2. Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Backend Framework** | FastAPI (Python 3.11+) | Async HTTP server, auto-generated OpenAPI docs |
| **ASGI Server** | Uvicorn | High-performance async server on port 8003 |
| **Database (Relational)** | PostgreSQL 15 + SQLAlchemy 2.0 (async) | ORM: users, findings, evidence, KB entries, engagements, audit logs |
| **Database (Vector)** | Qdrant | Vector similarity search for RAG retrieval |
| **Migrations** | Alembic | Schema versioning for PostgreSQL |
| **LLM Provider** | OpenRouter (free models) | AI orchestration via OpenRouter API |
| **Embedding Model** | BAAI/bge-base-en-v1.5 (768d) | Sentence embeddings for all KB text |
| **Frontend** | React 19 + TypeScript + Vite + TanStack Router + Tailwind CSS | Client-side SPA |
| **Reverse Proxy** | Nginx | Rate limiting, security headers, SSL termination |
| **Containerization** | Docker (PostgreSQL + Qdrant) | Local infra services |
| **Task Scheduler** | APScheduler | Background NVD/MITRE sync jobs |
| **Rate Limiting** | slowapi + Nginx | API rate limiting |

---

## 3. Database Schema (PostgreSQL — 6 tables)

```
users
├── id (UUID, PK)
├── email (unique, indexed)
├── hashed_password (bcrypt)
├── is_active (boolean)
└── created_at (datetime)

kb_entries                       ← Knowledge Base: CVEs + MITRE techniques + internal
├── id (UUID / CVE-ID / T-ID, PK)
├── entry_type (enum: "cve" | "attack_vector" | "internal")
├── title
├── description (text)
├── cvss_v3 (float, nullable)
├── cvss_v4 (float, nullable)
├── cwe (string, nullable)
├── mitre_techniques (JSON list)
├── affected_products (JSON list)
├── indicators (JSON list)
├── validation_steps (JSON list)
├── references (JSON list)
├── published_date (datetime, nullable)
├── last_modified (datetime, nullable)
└── qdrant_synced (boolean)

findings
├── id (UUID, PK)
├── title
├── description (text)
├── verdict (enum: confirmed | likely | insufficient | false_positive)
├── confidence (float 0-1)
├── reasoning (text)
├── matched_cves (JSON list)
├── matched_techniques (JSON list)
├── missing_evidence (JSON list)
├── recommended_next_steps (JSON list)
├── analyst_confirmed (boolean)
├── ghostwriter_finding_id (string, nullable)
├── created_at (datetime)
└── updated_at (datetime)
    └── evidence (relationship → Evidence)

evidence
├── id (UUID, PK)
├── finding_id (FK → findings.id)
├── filename
├── file_type (enum: image | pdf | text | log | binary)
├── storage_path
├── extracted_text (text, nullable)
├── image_description (text, nullable)
└── uploaded_at (datetime)

audit_logs
├── id (UUID, PK)
├── event_type (enum: validation | report_generated | ghostwriter_push | kb_add)
├── finding_id (nullable)
├── user_id (nullable)
├── input_hash (SHA-256 truncated)
├── payload_summary (JSON)
├── result_summary (JSON)
└── timestamp (datetime)

engagements
├── id (UUID, PK)
├── client_name
├── code (unique)
├── scope (text)
├── progress (float 0-1)
├── findings_count (integer)
├── lead (string)
├── status (enum: active | reporting | scoping)
├── start_date (datetime, nullable)
├── end_date (datetime, nullable)
├── created_at (datetime)
└── updated_at (datetime)
```

---

## 4. API Router Map (10 route groups)

| Prefix | Router File | Key Endpoints | Purpose |
|--------|------------|---------------|---------|
| `/auth` | `app/routers/auth.py` | POST `/register`, POST `/login` | JWT-based user auth |
| `/chat` | `app/routers/chat.py` | POST ``, POST `/stream` | RAG-enhanced chat (incl. SSE streaming) |
| `/validate` | `app/routers/validate.py` | POST `` | AI finding validation + evidence upload |
| `/findings` | `app/routers/findings.py` | GET ``, GET `/{id}`, POST ``, PATCH `/{id}`, DELETE `/{id}` | Full CRUD with pagination |
| `/kb` | `app/routers/kb.py` | GET `/entries`, GET `/entries/count`, GET `/search`, POST `/entries` | Knowledge base management + hybrid search |
| `/reports` | `app/routers/reports.py` | POST `/generate` | Programmatic DOCX report generation |
| `/engagements` | `app/routers/engagements.py` | GET ``, GET `/{id}`, POST ``, PATCH `/{id}`, DELETE `/{id}` | Engagement lifecycle management |
| `/ghostwriter` | `app/routers/ghostwriter.py` | GET `/projects`, POST `/push` | Push findings to external Ghostwriter |
| `/settings` | `app/routers/settings.py` | GET ``, PATCH `` | Runtime LLM provider + model config |
| `/audit` | `app/routers/audit.py` | GET `` | Audit trail listing |

---

## 5. Service Layer

### 5.1 `AsyncLLMClient` (`app/services/llm_client.py`)
- Unified async interface for LLM providers (code supports 6, configured for OpenRouter)
- **Active Provider**: OpenRouter with free models
  - Chat/Validation: `mistralai/mistral-7b-instruct:free`
  - Vision: `microsoft/phi-3-vision-128k-instruct:free`
- **Capabilities**: Chat generation, SSE streaming, structured validation output (JSON extraction), vision/image description
- **Resilience**: Exponential backoff retry (3 attempts), JSON extraction with regex fallback
- **Runtime overrides**: Reads active provider + model overrides from `settings.py` in-memory store

### 5.2 `validation_service` (`app/services/validation.py`)
- **RAG pipeline**: Hybrid search → KB context → LLM prompt → structured JSON verdict
- Returns: `{ verdict, confidence, reasoning, matched_cves, matched_techniques, missing_evidence, recommended_next_steps }`

### 5.3 `hybrid_search` (`app/services/retrieval.py`)
- **Vector search**: Embed query via BGE-base → Qdrant cosine similarity (top 20, threshold 0.15)
- **Keyword re-rank**: BM25-lite scoring with partial match awareness
- **Final score**: 70% vector + 30% keyword → top 7 results
- **Filters**: CVSS minimum, entry types

### 5.4 `evidence_parser` (`app/services/evidence.py`)
- Supports: text files (.txt/.log/.csv/.xml/.json/.yaml), images (.png/.jpg/.gif/.webp), PDFs (.pdf)
- **Images**: Sent to vision LLM for description
- **PDFs**: Text extracted via PyMuPDF
- **Security**: Path traversal sanitization, file size limits (10MB text, 20MB images, 50MB PDFs)

### 5.5 `report_generator` (`app/services/report.py`)
- Generates branded `.docx` reports with:
  - **Programmatic fields**: Verdict, CVSS, CVE IDs, MITRE techniques, confidence scores
  - **LLM-drafted prose**: Executive summary (3 paragraphs), per-finding risk context (2 sentences each)
  - Parallel LLM calls via `asyncio.gather`

### 5.6 `ghostwriter_client` (`app/services/ghostwriter_client.py`)
- GraphQL client for SpecterOps Ghostwriter reporting platform
- **Mutations**: Create finding, list projects
- Auth via Bearer token in `.env`

---

## 6. Ingestion Layer (Background Sync)

### 6.1 Embedder (`app/ingestion/embedder.py`)
- Model: **BAAI/bge-base-en-v1.5** (768-dimension embeddings)
- Pre-loaded at startup to avoid cold start latency
- **Chunking**: 400-char chunks with 80-char overlap for long texts
- **Mean pooling**: Averages embeddings across chunks
- **Deterministic Qdrant IDs**: MD5-based (survives process restarts)

### 6.2 NVD Sync (`app/ingestion/nvd_sync.py`) — runs every 6h
- Fetches CVEs modified in last 2 days
- Full pagination support (2000 per page)
- Upserts into PostgreSQL + Qdrant simultaneously
- Rate-limited: 6s without API key, 0.6s with key

### 6.3 MITRE ATT&CK Sync (`app/ingestion/mitre_sync.py`) — runs every 7d
- Downloads full enterprise-attack STIX bundle from MITRE CTI
- Parses attack patterns (techniques), detection text → validation steps
- Upserts into PostgreSQL + Qdrant

---

## 7. Frontend Architecture

### Tech Stack
- **Framework**: React 19 (TypeScript)
- **Build Tool**: Vite
- **Routing**: TanStack Router (file-based routes in `frontend/src/routes/`)
- **Styling**: Tailwind CSS
- **API Client**: Custom fetch wrapper (`frontend/src/lib/api.ts`)
- **UI Components**: shadcn/ui (Radix primitives + Tailwind)

### Routes (6 pages)
| Route | File | Purpose |
|-------|------|---------|
| `/` | `frontend/src/routes/index.tsx` | Dashboard / landing |
| `/chat` | `frontend/src/routes/chat.tsx` | RAG-enhanced chat with SSE streaming |
| `/knowledge` | `frontend/src/routes/knowledge.tsx` | Knowledge base browser (CVEs/MITRE/internal) |
| `/report` | `frontend/src/routes/report.tsx` | Report generation |
| `/settings` | `frontend/src/routes/settings.tsx` | LLM provider, model, runtime config |
| `/__root` | `frontend/src/routes/__root.tsx` | App shell, layout |

### API Client (`frontend/src/lib/api.ts`)
- Typed fetch wrapper with error handling
- SSE streaming for chat (abort controller support)
- FormData support for file uploads
- Auto-detects API base URL (same-origin for production, `localhost:8003` for dev)

---

## 8. Infrastructure & Deployment

### Docker Services (`docker-compose.yml`)
| Service | Image | Port | Volume |
|---------|-------|------|--------|
| `postgres` | postgres:15-alpine | 5432 | `postgres_data` |
| `qdrant` | qdrant/qdrant:latest | 6333, 6334 | `qdrant_data` |

### Nginx (`nginx.conf`)
- **Rate limiting**: 30 req/s zone, 50 burst, 20 concurrent connections
- **Upload limit**: 100 MB
- **Security headers**: X-Frame-Options, X-Content-Type-Options, XSS-Protection, HSTS
- **Proxies**: All API routes → backend, SPA fallback for root
- **SSE support**: `X-Accel-Buffering: no` for streaming chat

### Entry Point (`run.py`)
- `uvicorn.run("app.main:app", host="0.0.0.0", port=8003, reload=True)`

---

## 9. Data Flow: Finding Validation (Core Feature)

```
1. User submits finding (title + description + evidence files) → POST /validate
2. FastAPI /validate endpoint:
   a. Parse evidence files (text extraction / vision description)
   b. Call validate_finding() service
3. validation service:
   a. Hybrid search: embed query → Qdrant vector search → PostgreSQL KB load → re-rank
   b. Build prompt: system prompt + KB context + evidence + finding description
   c. AsyncLLMClient.generate_validation() → LLM responds with JSON
   d. Parse & validate JSON → ValidationResult schema
4. Persist: Create Finding record + Evidence records + AuditLog entry
5. Return verdict to frontend
```

---

## 10. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Fully async** (FastAPI + asyncpg + async LLM clients) | Non-blocking I/O prevents event loop starvation under concurrent requests |
| **OpenRouter-only** (free models) | No paid API keys required; `mistral-7b-instruct:free` for chat/validation, `phi-3-vision-128k-instruct:free` for vision |
| **Hybrid search** (vector + keyword) | Pure vector search misses exact CVE/technique ID matches; keyword scoring fixes this |
| **Pre-loaded embedding model** | Cold start for SentenceTransformer is ~30 seconds; unacceptable at first request |
| **Deterministic Qdrant IDs** | Python `hash()` changes between processes; MD5 ensures stable point IDs across restarts |
| **Separate model per role** (chat/validation/vision) | Each task has different cost/quality requirements (e.g., cheap model for chat, strong model for validation) |
| **In-memory runtime settings** | Avoids DB round-trip for every LLM call; restarts reset to .env defaults |

---

## 11. Security Considerations

- **JWT authentication** (bcrypt passwords, HS256 tokens, 480min expiry)
- **Path traversal prevention** (`Path(filename).name` on all uploaded files)
- **File size limits** enforced at multiple layers (Nginx 100MB, FastAPI 50MB total, per-file caps)
- **Audit logging** for all validation and ghostwriter operations (tamper-evident via input_hash)
- **API key removal**: `/settings/api-key` endpoint removed (security risk)
- **Rate limiting** at both Nginx and FastAPI (slowapi) layers
- **CORS**: Wide-open for development (`allow_origins=["*"]`)

---

## 12. File Map (Full Project)

```
Mazars_rag/
├── run.py                              # App entry point (uvicorn :8003)
├── requirements.txt                    # Python dependencies
├── docker-compose.yml                  # PostgreSQL + Qdrant containers
├── nginx.conf                          # Reverse proxy config
├── alembic.ini                         # Alembic config
├── .env                                # Environment variables (secrets)
├── .env.example                        # Template for .env
├── plan.md                             # Development plan
├── TODO.md                             # Task tracking
├── test.db                             # SQLite fallback for testing
├── alembic/
│   ├── env.py                          # Async Alembic environment
│   ├── script.py.mako                  # Migration template
│   └── versions/                       # DB migration scripts
├── app/
│   ├── __init__.py
│   ├── main.py                         # FastAPI app factory, lifespan, routers
│   ├── config.py                       # Pydantic Settings (all env vars)
│   ├── db.py                           # SQLAlchemy async engine + session
│   ├── models.py                       # 6 ORM models (User, KBEntry, Finding, Evidence, AuditLog, Engagement)
│   ├── schemas.py                      # Pydantic request/response schemas
│   ├── auth.py                         # JWT utilities (hash, verify, create_token)
│   ├── routers/
│   │   ├── auth.py                     # /auth (register, login)
│   │   ├── chat.py                     # /chat (JSON + SSE streaming)
│   │   ├── validate.py                 # /validate (finding validation + file upload)
│   │   ├── findings.py                 # /findings CRUD
│   │   ├── kb.py                       # /kb (knowledge base CRUD + search)
│   │   ├── reports.py                  # /reports (DOCX generation)
│   │   ├── engagements.py              # /engagements CRUD
│   │   ├── ghostwriter.py              # /ghostwriter (push to external tool)
│   │   ├── settings.py                 # /settings (runtime config)
│   │   └── audit.py                    # /audit (log listing)
│   ├── services/
│   │   ├── llm_client.py              # Async multi-provider LLM client
│   │   ├── validation.py              # RAG + LLM validation pipeline
│   │   ├── retrieval.py               # Hybrid search (Qdrant + keyword)
│   │   ├── evidence.py                # File parsing (text, image, PDF)
│   │   ├── report.py                  # DOCX report generation
│   │   └── ghostwriter_client.py      # GraphQL client for Ghostwriter
│   └── ingestion/
│       ├── embedder.py                # Vector embedding (BGE-base)
│       ├── nvd_sync.py                # NVD CVE sync (every 6h)
│       └── mitre_sync.py              # MITRE ATT&CK sync (every 7d)
└── frontend/
    ├── package.json                   # Node dependencies
    ├── vite.config.ts                 # Vite config
    ├── tsconfig.json                  # TypeScript config
    └── src/
        ├── router.tsx                 # TanStack Router setup
        ├── routeTree.gen.ts           # Auto-generated route tree
        ├── styles.css                 # Global styles
        ├── lib/
        │   └── api.ts                 # Typed API client (fetch + SSE)
        ├── components/                # UI components (shadcn/ui)
        │   ├── ui/
        │   ├── layout/
        │   └── brand/
        ├── hooks/
        ├── routes/
        │   ├── __root.tsx             # App shell
        │   ├── index.tsx              # Dashboard
        │   ├── chat.tsx               # RAG chat page
        │   ├── knowledge.tsx          # Knowledge base browser
        │   ├── report.tsx             # Report generation
        │   └── settings.tsx           # Settings page
        └── assets/                    # Static assets (logos, etc.)