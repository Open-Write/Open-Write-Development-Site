"""Central configuration for the Open-Write Web backend.

Reads from environment variables (see backend/.env, loaded by main.py). All
values have sensible defaults for local development but MUST be overridden in
production via the systemd unit's EnvironmentFile.
"""
from __future__ import annotations

import os
from pathlib import Path

# PostgreSQL connection string (dedicated conversation database).
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://role_9d2430a95:U2DX7xuq_pKXol7dRejhVTjKVyhucpDs@"
    "db-9d2430a95.db006.hosteddb.reai.io:5432/9d2430a95?connect_timeout=15",
)

# Secret used to sign JWT tokens. Override in production.
JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production-openwrite-web")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "168"))  # 7 days

# Root directory for user-scoped project storage.
DATA_ROOT = Path(os.environ.get("OPENWRITE_DATA", "/home/ubuntu/openwrite_data"))

# Read-only reference tree holding the canonical pipeline rule files.
OPENWRITE_REFERENCE = os.environ.get(
    "OPENWRITE_REFERENCE",
    "/home/ubuntu/github_repos/open-write-studio/openwrite",
)

# CORS: allowed frontend origins (comma-separated). "*" allows any.
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")


def user_projects_dir(user_id: str) -> Path:
    """Directory holding all of one user's projects."""
    return DATA_ROOT / "users" / str(user_id) / "projects"


def project_path(user_id: str, project_id: str) -> Path:
    """Absolute on-disk path for a single project.

    If the project was imported with a ``source_path``, that directory is used
    directly (files are not copied).  Otherwise the default data-root layout
    applies.
    """
    # Avoid circular import — db is imported later.
    from app import db
    row = db.query_one(
        "SELECT source_path FROM projects WHERE id = %s AND user_id = %s",
        (project_id, user_id),
    )
    if row and row.get("source_path"):
        sp = Path(row["source_path"])
        if sp.is_dir():
            return sp
    return user_projects_dir(user_id) / str(project_id)
