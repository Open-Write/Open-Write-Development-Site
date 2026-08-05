"""Open-Write Web — FastAPI entry point.

Multi-tenant web version of the Open-Write creative-writing pipeline. Wires
JWT auth, user-scoped projects, the ported pipeline orchestrator, and the new
version-tracking feature.
"""
from __future__ import annotations

import os

# Ensure the pipeline finds the canonical rule files before any pipeline import.
os.environ.setdefault(
    "OPENWRITE_REFERENCE",
    "/home/ubuntu/github_repos/open-write-studio/openwrite",
)

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.config import CORS_ORIGINS
from app.routers import (
    admin_router,
    auth_router,
    editorial_router,
    projects_router,
    settings_router,
    versions_router,
    pipeline_router,
    writing_router,
)


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
    return {"status": "ok", "app": "Open-Write Web API", "version": "1.0.0"}


@app.get("/api/health")
async def api_health_check():
    return {"status": "ok", "app": "Open-Write Web API", "version": "1.0.0"}


app.include_router(admin_router.router)
app.include_router(auth_router.router)
app.include_router(editorial_router.router)
app.include_router(projects_router.router)
app.include_router(settings_router.router)
app.include_router(versions_router.router)
app.include_router(pipeline_router.router)
app.include_router(writing_router.router)
