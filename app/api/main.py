"""
app/api/main.py
───────────────────────────────────────────────────────────────────────────────
FastAPI application entry point (Tasks 10 & 11).

Startup
-------
    uvicorn app.api.main:app --reload

CORS is enabled for all origins listed in ``CORS_ORIGINS`` (comma-separated).
The default is ``http://localhost:5173`` so the Vite React dev server works
out of the box.

Routers
-------
  /sessions          → routes_sessions.router
  /documents         → routes_documents.router
  /health            → inline endpoint (avoids a separate file for one route)

Seed documents (Task 11)
------------------------
On startup, ``seed_once()`` is called in a background thread.  It is
idempotent – if all 4 seed documents are already ``ready`` it returns
immediately.  The HTTP server is not blocked; requests are accepted while
seeding runs in parallel.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_documents import router as documents_router
from app.api.routes_sessions import router as sessions_router
from app.config import settings
from app.db.engine import check_connection
from app.index import ping_chroma

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan (startup / shutdown)
# ─────────────────────────────────────────────────────────────────────────────

def _run_seed_in_background() -> None:
    """
    Import seed lazily (avoids circular-import issues at module load time)
    and call seed_once() in a daemon thread so the HTTP server starts
    immediately and is not blocked by PDF processing.
    """
    try:
        from app.seed import seed_once  # local import to avoid circulars
        seed_once()
    except Exception:
        logger.exception("seed: background seed run raised an unhandled exception")


@asynccontextmanager
async def _lifespan(application: FastAPI):
    """FastAPI lifespan hook – runs seed on first startup."""
    logger.info("startup: launching seed_once() in background thread")
    t = threading.Thread(target=_run_seed_in_background, daemon=True, name="seed-thread")
    t.start()
    yield
    # Shutdown: nothing to clean up (daemon thread exits with process)
    logger.info("shutdown: API server stopping")


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Fin-Research API",
    description=(
        "Multi-Agent Financial Research System — "
        "Document upload, parsing, embedding and vector indexing."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=_lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(sessions_router, prefix="/sessions", tags=["sessions"])
app.include_router(documents_router, prefix="/documents", tags=["documents"])


# ─────────────────────────────────────────────────────────────────────────────
# Health endpoint
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["health"], summary="API and dependency health check")
def health() -> dict:
    """
    Returns the health of the API and its critical dependencies.

    Response fields
    ---------------
    status      : ``"ok"`` always (if the API process is up, this endpoint responds).
    database    : ``true`` if Postgres is reachable.
    chroma      : ``true`` if Chroma Cloud is reachable.
    """
    db_ok = check_connection()
    chroma_ok = ping_chroma()
    return {
        "status": "ok",
        "database": db_ok,
        "chroma": chroma_ok,
    }
