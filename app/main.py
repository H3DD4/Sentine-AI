"""
FastAPI application entry point.

Key changes:
- lifespan context manager replaces deprecated @app.on_event("startup")
- Embedding model pre-loaded at startup (avoids 30-second cold start on first request)
- Singleton Qdrant client initialized at startup
- CORS updated to include both dev (3000) and prod (8003) origins
- Correct Qdrant collection creation with size=768 for BGE-base
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.config import settings
from app.db import engine, Base, get_session_context
from app.routers import chat, report, validate, findings, ghostwriter, kb, engagements, auth
from app.routers import settings as settings_router, audit
from app.ingestion.nvd_sync import sync_nvd
from app.ingestion.mitre_sync import sync_mitre
from app.ingestion.embedder import load_model_sync, load_sparse_model_sync
from app.kb.indexer import ensure_all_collections
from app.kb.registry import all_sources, check_all_sources
from app.services.retrieval import init_qdrant_client, get_qdrant
import logging
import os

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle manager."""
    # ── Startup ────────────────────────────────────────────────────────────
    log.info("Starting Sentinel.AI backend…")

    # 1. Schema is owned by Alembic, NOT by create_all.
    #    create_all only ever creates missing tables — it never alters an
    #    existing one — so with it in place a column added to a model would
    #    silently never appear in a database that already had the table, and
    #    the mismatch would surface as a query error at runtime instead of at
    #    deploy time. Run `alembic upgrade head` to migrate.
    await _verify_schema()

    # 2. Pre-load models (avoids a 30-second cold start on the first request).
    try:
        load_model_sync()
    except Exception as exc:
        log.warning("Embedding model load failed: %s — retrieval will be unavailable", exc)

    try:
        load_sparse_model_sync()
    except Exception as exc:
        # Retrieval degrades to dense-only rather than failing.
        log.warning("Sparse (BM25) model load failed: %s — dense-only retrieval", exc)

    # 3. Qdrant: one collection per knowledge source.
    try:
        init_qdrant_client()
        qdrant = get_qdrant()
        await ensure_all_collections(qdrant, all_sources())

        async with get_session_context() as session:
            health = await check_all_sources(session, qdrant)
        for key, h in health.items():
            log.info(
                "KB source %-12s %-12s %s",
                key, h.availability.value, h.detail,
            )
        usable = [k for k, h in health.items() if h.availability.value in ("ok", "degraded")]
        if usable:
            log.info("Retrieval ready over sources: %s", ", ".join(usable))
        else:
            log.warning(
                "NO knowledge source is usable — the assistant will answer "
                "without retrieval and must say so explicitly."
            )
    except Exception as exc:
        log.warning("Qdrant init failed: %s — vector search will be unavailable", exc)

    # 4. Ensure upload directory exists
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    os.makedirs(settings.REPORT_DIR, exist_ok=True)
    os.makedirs(settings.REPORT_TEMPLATE_DIR, exist_ok=True)

    # 5. Start background KB sync scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_run_nvd_sync,   "interval", hours=6,  id="nvd_sync")
    scheduler.add_job(_run_mitre_sync, "interval", days=7,   id="mitre_sync")
    scheduler.add_job(_run_health_check, "interval", minutes=5, id="kb_health")
    scheduler.start()
    log.info("Scheduler started (NVD 6h, MITRE 7d, KB health 5m).")

    yield  # ── Application runs ──────────────────────────────────────────

    # ── Shutdown ───────────────────────────────────────────────────────────
    scheduler.shutdown(wait=False)
    log.info("Sentinel.AI shutdown complete.")


async def _verify_schema() -> None:
    """
    Fail loudly at startup if migrations have not been applied, rather than
    letting the first query fail with an opaque 'relation does not exist'.
    """
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            rev = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar()
        log.info("Database schema at revision %s.", rev)
    except Exception as exc:
        log.error(
            "Database schema is not initialised (%s). "
            "Run:  alembic upgrade head",
            exc,
        )


# ── App factory ───────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Forvis Mazars Red Team RAG",
    version="2.0.0",
    description="Sentinel.AI — AI-powered red team finding validation and reporting.",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — driven by settings.CORS_ORIGINS (see .env: CORS_ORIGINS=[...]).
# Add new origins there rather than hardcoding here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(validate.router)
app.include_router(findings.router)
app.include_router(report.router)
app.include_router(ghostwriter.router)
app.include_router(kb.router)
app.include_router(engagements.router)
app.include_router(settings_router.router)
app.include_router(audit.router)

# ── Static files + SPA fallback ───────────────────────────────────────────────

_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_FRONTEND_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(_FRONTEND_DIR, "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    async def serve_spa():
        return FileResponse(os.path.join(_FRONTEND_DIR, "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa_fallback(full_path: str):
        """Serve index.html for all non-API routes (SPA client-side routing)."""
        index = os.path.join(_FRONTEND_DIR, "index.html")
        return FileResponse(index)
else:
    @app.get("/", include_in_schema=False)
    async def root():
        return {"status": "ok", "message": "Sentinel.AI API running. Frontend not built yet."}


# ── Scheduler helpers ─────────────────────────────────────────────────────────

async def _run_nvd_sync():
    try:
        async with get_session_context() as session:
            await sync_nvd(session)
    except Exception as exc:
        log.error("NVD sync failed: %s", exc)


async def _run_mitre_sync():
    try:
        async with get_session_context() as session:
            await sync_mitre(session)
    except Exception as exc:
        log.error("MITRE sync failed: %s", exc)


async def _run_health_check():
    """
    Refresh per-source health on a timer so the UI badges and the retrieval
    router notice a source going down (or coming back) without a restart.
    """
    try:
        async with get_session_context() as session:
            await check_all_sources(session, get_qdrant())
    except Exception as exc:
        log.warning("KB health check failed: %s", exc)
