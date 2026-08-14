"""Open-Write Web — FastAPI entry point.

Multi-tenant web version of the Open-Write creative-writing pipeline. Wires
JWT auth, user-scoped projects, the ported pipeline orchestrator, and the new
version-tracking feature.
"""
from __future__ import annotations

import os
from pathlib import Path

# Load .env file before any config imports so environment variables are set.
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Configure logging so application log.info() calls are visible in Railway logs.
# Uvicorn configures its own access logger but leaves the root logger at WARNING,
# which silently drops all INFO-level messages from application modules.
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

# Resolve paths relative to the repo root (two levels up from this file).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Ensure the pipeline finds the canonical rule files before any pipeline import.
os.environ.setdefault(
    "OPENWRITE_REFERENCE",
    str(_REPO_ROOT / "openwrite"),
)

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from app import db
from app.config import CORS_ORIGINS
from app.routers import (
    admin_router,
    auth_router,
    editorial_router,
    file_browser_router,
    help_router,
    projects_router,
    settings_router,
    versions_router,
    pipeline_router,
    writing_router,
)

# ── Static asset directories ──────────────────────────────────────────────
_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"
_MARKETING_DIR = _REPO_ROOT / "marketing"


def _mime_for(path: Path) -> str | None:
    """Return a content-type string for common static file extensions."""
    _TYPES = {
        ".css": "text/css",
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".ico": "image/x-icon",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".eot": "application/vnd.ms-fontobject",
        ".mp3": "audio/mpeg",
        ".mp4": "video/mp4",
        ".webp": "image/webp",
        ".txt": "text/plain",
        ".xml": "application/xml",
        ".pdf": "application/pdf",
    }
    return _TYPES.get(path.suffix.lower())


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_pool()
    yield
    db.close_pool()


app = FastAPI(
    title="Open-Write Web API",
    description="Multi-tenant web backend for the Open-Write writing pipeline.",
    version="1.0.0",
    lifespan=lifespan,
)

_origins = ["*"] if CORS_ORIGINS.strip() == "*" else [
    o.strip() for o in CORS_ORIGINS.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False if _origins == ["*"] else True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "app": "Open-Write Web API",
        "version": "1.0.0",
        "repo_root": str(_REPO_ROOT),
        "frontend_exists": _FRONTEND_DIST.is_dir(),
        "marketing_exists": _MARKETING_DIR.is_dir(),
        "index_exists": (_FRONTEND_DIST / "index.html").is_file(),
        "cwd": os.getcwd(),
    }


@app.get("/api/health")
async def api_health_check():
    return {"status": "ok", "app": "Open-Write Web API", "version": "1.0.0"}


# ── API routes (mounted first so they take priority) ──────────────────────
app.include_router(admin_router.router)
app.include_router(auth_router.router)
app.include_router(editorial_router.router)
app.include_router(file_browser_router.router)
app.include_router(projects_router.router)
app.include_router(settings_router.router)
app.include_router(versions_router.router)
app.include_router(pipeline_router.router)
app.include_router(writing_router.router)
app.include_router(help_router.router)


def _serve_file(path: Path, media_type: str | None = None) -> FileResponse | HTMLResponse:
    """Serve a file if it exists, otherwise return a 404 with diagnostic info."""
    if path.is_file():
        return FileResponse(path, media_type=media_type or _mime_for(path))
    return HTMLResponse(
        f"<h1>File not found</h1><p>Expected at: {path}</p>"
        f"<p>Exists: {path.exists()}</p>"
        f"<p>Parent exists: {path.parent.exists()}</p>",
        status_code=404,
    )


# ── Frontend SPA at /studio/ ─────────────────────────────────────────────
@app.get("/studio")
async def studio_root():
    return _serve_file(_FRONTEND_DIST / "index.html", "text/html")


@app.get("/studio/{full_path:path}")
async def studio_spa(full_path: str):
    candidate = _FRONTEND_DIST / full_path
    if candidate.is_file():
        return FileResponse(candidate, media_type=_mime_for(candidate))
    return _serve_file(_FRONTEND_DIST / "index.html", "text/html")


# ── Marketing site at / ──────────────────────────────────────────────────
@app.get("/{full_path:path}")
async def marketing_catch_all(full_path: str):
    if not full_path or full_path == "":
        full_path = "index.html"
    candidate = _MARKETING_DIR / full_path
    if candidate.is_file():
        return FileResponse(candidate, media_type=_mime_for(candidate))
    html_candidate = _MARKETING_DIR / (full_path + ".html")
    if html_candidate.is_file():
        return FileResponse(html_candidate, media_type="text/html")
    return _serve_file(_MARKETING_DIR / "index.html", "text/html")
