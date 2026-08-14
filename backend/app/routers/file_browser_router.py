"""File browser routes — project-wide file tree, read/write/create/delete/rename.

Exposes the entire project directory to the frontend Studio editor so users can
open any file (markdown, text, profiles, outlines, etc.) in the document editor.
"""
from __future__ import annotations

import mimetypes
import os
import shutil

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth, config, db

router = APIRouter(prefix="/api/files", tags=["files"])

# Extensions treated as editable text files.
_TEXT_EXTS = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".tsv",
    ".xml", ".html", ".htm", ".css", ".js", ".ts", ".py", ".sh", ".bat",
    ".ps1", ".cfg", ".ini", ".env", ".log", ".rtf", ".opml", ".fountain",
}

# Extensions that should be returned as base64 data-URI images.
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}


def _resolve_project(project_id: str, user_id: str) -> str:
    row = db.query_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (project_id, user_id),
    )
    if not row:
        raise HTTPException(404, "Project not found.")
    return os.path.realpath(str(config.project_path(user_id, project_id)))


def _safe_join(project: str, rel: str) -> str:
    full = os.path.realpath(os.path.join(project, rel))
    if not full.startswith(project + os.sep) and full != project:
        raise HTTPException(400, "Invalid path.")
    return full


def _classify(ext: str) -> str:
    ext = ext.lower()
    if ext in _TEXT_EXTS:
        return "text"
    if ext in _IMAGE_EXTS:
        return "image"
    return "binary"


def _build_tree(root: str, base: str) -> list[dict]:
    """Recursively build a file-tree structure."""
    entries: list[dict] = []
    try:
        items = sorted(os.listdir(root), key=lambda x: (not os.path.isdir(os.path.join(root, x)), x.lower()))
    except OSError:
        return entries
    for name in items:
        full = os.path.join(root, name)
        rel = os.path.relpath(full, base).replace("\\", "/")
        if os.path.isdir(full):
            children = _build_tree(full, base)
            entries.append({
                "name": name,
                "path": rel,
                "type": "directory",
                "children": children,
            })
        else:
            ext = os.path.splitext(name)[1]
            entries.append({
                "name": name,
                "path": rel,
                "type": "file",
                "kind": _classify(ext),
                "size": os.path.getsize(full),
            })
    return entries


# ── List project file tree ────────────────────────────────────────────────
@router.get("/{project_id}/tree")
async def file_tree(project_id: str, current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    if not os.path.isdir(project):
        raise HTTPException(404, "Project directory not found.")
    tree = _build_tree(project, project)
    return {"root": project, "tree": tree}


# ── Read a file ──────────────────────────────────────────────────────────
@router.get("/{project_id}/read")
async def read_file(project_id: str, path: str,
                    current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    full = _safe_join(project, path)
    if not os.path.isfile(full):
        raise HTTPException(404, "File not found.")
    ext = os.path.splitext(full)[1].lower()
    kind = _classify(ext)
    if kind == "text":
        try:
            with open(full, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(full, "r", encoding="latin-1") as f:
                content = f.read()
        return {
            "path": os.path.relpath(full, project).replace("\\", "/"),
            "content": content,
            "kind": "text",
            "size": os.path.getsize(full),
            "word_count": len(content.split()),
        }
    elif kind == "image":
        import base64
        mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        return {
            "path": os.path.relpath(full, project).replace("\\", "/"),
            "content": f"data:{mime};base64,{data}",
            "kind": "image",
            "size": os.path.getsize(full),
        }
    else:
        return {
            "path": os.path.relpath(full, project).replace("\\", "/"),
            "content": None,
            "kind": "binary",
            "size": os.path.getsize(full),
        }


# ── Save a file ──────────────────────────────────────────────────────────
class SaveFileRequest(BaseModel):
    path: str
    content: str


@router.post("/{project_id}/save")
async def save_file(project_id: str, req: SaveFileRequest,
                    current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    full = _safe_join(project, req.path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(req.content)
    rel = os.path.relpath(full, project).replace("\\", "/")
    return {
        "saved": True,
        "path": rel,
        "size": os.path.getsize(full),
        "word_count": len(req.content.split()),
    }


# ── Create file or directory ─────────────────────────────────────────────
class CreateItemRequest(BaseModel):
    path: str
    is_directory: bool = False
    content: str = ""


@router.post("/{project_id}/create")
async def create_item(project_id: str, req: CreateItemRequest,
                      current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    full = _safe_join(project, req.path)
    if os.path.exists(full):
        raise HTTPException(409, "Item already exists.")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if req.is_directory:
        os.makedirs(full, exist_ok=True)
    else:
        with open(full, "w", encoding="utf-8") as f:
            f.write(req.content)
    rel = os.path.relpath(full, project).replace("\\", "/")
    return {"created": True, "path": rel, "is_directory": req.is_directory}


# ── Delete file or directory ─────────────────────────────────────────────
@router.delete("/{project_id}/delete")
async def delete_item(project_id: str, path: str,
                      current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    full = _safe_join(project, path)
    if not os.path.exists(full):
        raise HTTPException(404, "Item not found.")
    if os.path.isdir(full):
        shutil.rmtree(full)
    else:
        os.remove(full)
    return {"deleted": True, "path": path}


# ── Rename / move ────────────────────────────────────────────────────────
class RenameItemRequest(BaseModel):
    old_path: str
    new_path: str


@router.post("/{project_id}/rename")
async def rename_item(project_id: str, req: RenameItemRequest,
                      current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    old_full = _safe_join(project, req.old_path)
    new_full = _safe_join(project, req.new_path)
    if not os.path.exists(old_full):
        raise HTTPException(404, "Source not found.")
    if os.path.exists(new_full):
        raise HTTPException(409, "Destination already exists.")
    os.makedirs(os.path.dirname(new_full), exist_ok=True)
    os.rename(old_full, new_full)
    return {
        "renamed": True,
        "old_path": req.old_path,
        "new_path": os.path.relpath(new_full, project).replace("\\", "/"),
    }
