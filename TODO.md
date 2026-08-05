# Sentinel.AI — Full Integration TODO

## Phase 1: API Client Layer ✅
- [x] Create `frontend/src/lib/api.ts` — typed API client for all endpoints

## Phase 2: Backend Enhancements ✅
- [x] Add `Engagement` model to `app/models.py`
- [x] Add `Engagement` schemas to `app/schemas.py`
- [x] Create `app/routers/engagements.py` — Engagement CRUD
- [x] Add `GET /kb/entries` + `GET /kb/search` to `app/routers/kb.py`
- [x] Add `POST /findings` + `DELETE /findings/:id` to `app/routers/findings.py`
- [x] Add settings endpoints to `app/routers/settings.py` (NEW FILE)
- [x] Register all new routers in `app/main.py`

## Phase 3: Connect Dashboard (/) ✅
- [x] Update `frontend/src/routes/index.tsx` — real API calls for findings & engagements

## Phase 4: Connect Chat (/chat) ✅
- [x] Rewrite `frontend/src/routes/chat.tsx` — real POST /chat, POST /validate, GET /findings/:id

## Phase 5: Connect Knowledge Base (/knowledge) ✅
- [x] Rewrite `frontend/src/routes/knowledge.tsx` — real GET /kb/search, POST /kb/entries

## Phase 6: Connect Report Builder (/report) ✅
- [x] Rewrite `frontend/src/routes/report.tsx` — real POST /reports/generate with file download

## Phase 7: Connect Settings (/settings) ✅
- [x] Rewrite `frontend/src/routes/settings.tsx` — real API calls

## Phase 8: Polish & UX ✅
- [x] Proper TypeScript types alignment — Fix Finding + KBEntry types in api.ts
- [x] Create reusable loading skeleton components
- [x] Wire up Toaster in __root.tsx for global toast access
- [x] Dashboard (/) — Replace spinners with skeletons, fix as any casts
- [x] Chat (/chat) — Toast notifications for mutations, ChatSkeleton
- [x] Knowledge (/knowledge) — Card skeleton grid replacing Loader2 spinner
- [x] Report (/report) — List skeleton for findings loading, toast for download
- [x] Settings (/settings) — Form skeleton on load, toast on save, spinner replaced

## Multi-LLM Provider Support ✅
- [x] `requirements.txt` — Add openai, google-genai, together packages
- [x] `app/config.py` — Add multi-provider config (OpenAI, Together, OpenRouter, Ollama, Gemini)
- [x] `app/services/llm_client.py` (NEW) — Factory with unified generate() and generate_with_vision()
- [x] `app/services/validation.py` — Use new llm_client instead of hardcoded Anthropic
- [x] `app/routers/chat.py` — Use new llm_client instead of hardcoded Anthropic
- [x] `app/schemas.py` — Add provider fields to SettingsOut/SettingsUpdate
- [x] `app/routers/settings.py` — Handle multiple provider settings
- [x] `frontend/src/lib/api.ts` — Update AppSettings type with provider info
- [x] `frontend/src/routes/settings.tsx` — Add provider selection + conditional model lists
- [ ] Verify — Run backend and test provider switching
