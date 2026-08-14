"""Project CRUD routes. Projects are user-scoped; on-disk storage lives at
DATA_ROOT/users/{user_id}/projects/{project_id}/."""
from __future__ import annotations

import os
import shutil

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth, config, db

router = APIRouter(prefix="/api/projects", tags=["projects"])

VALID_FORMATS = {"novel", "screenplay", "tv"}


class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""
    format: str = "novel"


def _get_owned_project(project_id: str, user_id: str) -> dict:
    row = db.query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s",
        (project_id, user_id),
    )
    if not row:
        raise HTTPException(404, "Project not found.")
    return row


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row.get("description") or "",
        "format": row.get("format") or "novel",
        "source_path": row.get("source_path"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


@router.post("")
async def create_project(req: CreateProjectRequest, current=Depends(auth.get_current_user)):
    fmt = req.format if req.format in VALID_FORMATS else "novel"
    if not req.name.strip():
        raise HTTPException(400, "Project name is required.")
    row = db.execute(
        "INSERT INTO projects (user_id, name, description, format) "
        "VALUES (%s, %s, %s, %s) RETURNING *",
        (current["id"], req.name.strip(), req.description.strip(), fmt),
    )
    # Create the on-disk project scaffold.
    pdir = config.project_path(current["id"], str(row["id"]))
    for sub in ("bible", "manuscript", "critic_outputs", "coverage_reports",
                "state", "profiles"):
        (pdir / sub).mkdir(parents=True, exist_ok=True)
    return _serialize(row)


# ── Import existing project from a local directory ───────────────────────
EXPECTED_DIRS = {"bible", "manuscript", "profiles", "critic_outputs",
                 "coverage_reports", "state", "notes", "summaries"}


class ImportProjectRequest(BaseModel):
    name: str
    source_path: str
    description: str = ""
    format: str = "novel"


@router.post("/import")
async def import_project(req: ImportProjectRequest,
                         current=Depends(auth.get_current_user)):
    if not req.name.strip():
        raise HTTPException(400, "Project name is required.")
    source = os.path.realpath(req.source_path.strip())
    if not os.path.isdir(source):
        raise HTTPException(400, f"Directory not found: {source}")

    # Detect format from directory contents if not explicitly provided.
    fmt = req.format if req.format in VALID_FORMATS else "novel"
    if req.format == "novel":
        # Auto-detect screenplay / tv if matching files exist.
        for f in os.listdir(source):
            low = f.lower()
            if low.endswith(".fountain") or "screenplay" in low:
                fmt = "screenplay"
                break
            if low.endswith(".tv") or "tv_script" in low:
                fmt = "tv"
                break

    # Scan what's inside the source directory.
    found_dirs = set()
    file_count = 0
    for entry in os.scandir(source):
        if entry.is_dir():
            found_dirs.add(entry.name.lower())
        elif entry.is_file():
            file_count += 1

    recognized = found_dirs & EXPECTED_DIRS
    if not recognized and file_count == 0:
        raise HTTPException(
            400,
            "The directory appears empty. Expected an Open-Write project "
            "structure (bible/, manuscript/, profiles/, etc.).",
        )

    # Create the DB record with source_path.
    row = db.execute(
        "INSERT INTO projects (user_id, name, description, format, source_path) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING *",
        (current["id"], req.name.strip(), req.description.strip(), fmt, source),
    )

    # Ensure standard subdirectories exist in the source (non-destructive).
    for sub in ("bible", "manuscript", "critic_outputs", "coverage_reports",
                "state", "profiles"):
        os.makedirs(os.path.join(source, sub), exist_ok=True)

    result = _serialize(row)
    result["recognized_dirs"] = sorted(recognized)
    result["file_count"] = file_count
    return result


@router.get("")
async def list_projects(current=Depends(auth.get_current_user)):
    rows = db.query_all(
        "SELECT * FROM projects WHERE user_id = %s ORDER BY updated_at DESC",
        (current["id"],),
    )
    return [_serialize(r) for r in rows]


@router.get("/{project_id}")
async def get_project(project_id: str, current=Depends(auth.get_current_user)):
    row = _get_owned_project(project_id, current["id"])
    return _serialize(row)


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    description: str | None = None


@router.put("/{project_id}")
async def update_project(project_id: str, req: UpdateProjectRequest,
                         current=Depends(auth.get_current_user)):
    _get_owned_project(project_id, current["id"])
    fields, params = [], []
    if req.name is not None and req.name.strip():
        fields.append("name = %s")
        params.append(req.name.strip())
    if req.description is not None:
        fields.append("description = %s")
        params.append(req.description.strip())
    if not fields:
        raise HTTPException(400, "Nothing to update.")
    fields.append("updated_at = NOW()")
    params.extend([project_id, current["id"]])
    row = db.execute(
        f"UPDATE projects SET {', '.join(fields)} "
        f"WHERE id = %s AND user_id = %s RETURNING *",
        tuple(params),
    )
    return _serialize(row)


@router.delete("/{project_id}")
async def delete_project(project_id: str, current=Depends(auth.get_current_user)):
    _get_owned_project(project_id, current["id"])
    db.execute("DELETE FROM projects WHERE id = %s AND user_id = %s",
               (project_id, current["id"]))
    # Remove on-disk data (best-effort).
    pdir = config.project_path(current["id"], project_id)
    if pdir.exists():
        shutil.rmtree(pdir, ignore_errors=True)
    return {"deleted": True, "id": project_id}
