"""Version history routes — browse captured snapshots.

- GET /api/versions/{project_id}                       -> grouped list (no content)
- GET /api/versions/{project_id}/history/{ct}/{chapter} -> all versions of one piece
- GET /api/versions/detail/{version_id}                -> full content of one version
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import auth, db

router = APIRouter(prefix="/api/versions", tags=["versions"])


def _assert_owns_project(project_id: str, user_id: str) -> None:
    row = db.query_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (project_id, user_id),
    )
    if not row:
        raise HTTPException(404, "Project not found.")


def _row_summary(r: dict) -> dict:
    return {
        "id": str(r["id"]),
        "phase": r["phase"],
        "chapter_number": r["chapter_number"],
        "content_type": r["content_type"],
        "word_count": r["word_count"],
        "critic_verdict": r["critic_verdict"],
        "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
    }


@router.get("/{project_id}")
async def list_versions(project_id: str, current=Depends(auth.get_current_user)):
    """Return all versions for a project grouped by chapter then content_type.

    Group key: chapter number (or "project" for chapterless phases). Within a
    group, versions are grouped by content_type, newest first.
    """
    _assert_owns_project(project_id, current["id"])
    rows = db.query_all(
        "SELECT id, phase, chapter_number, content_type, word_count, "
        "critic_verdict, created_at FROM versions "
        "WHERE project_id = %s ORDER BY created_at DESC",
        (project_id,),
    )
    groups: dict[str, dict] = {}
    for r in rows:
        ch = r["chapter_number"]
        gkey = f"Chapter {ch}" if ch is not None else "Project-level"
        g = groups.setdefault(gkey, {"group": gkey, "chapter_number": ch, "items": {}})
        ct = r["content_type"]
        g["items"].setdefault(ct, []).append(_row_summary(r))
    # Convert items dict to a stable list.
    ordered = []
    # Project-level first, then chapters ascending.
    def _sort_key(k: str):
        g = groups[k]
        return (0, -1) if g["chapter_number"] is None else (1, g["chapter_number"])
    for k in sorted(groups.keys(), key=_sort_key):
        g = groups[k]
        g["items"] = [
            {"content_type": ct, "versions": v} for ct, v in g["items"].items()
        ]
        ordered.append(g)
    return {"groups": ordered, "total": len(rows)}


@router.get("/{project_id}/history/{content_type}/{chapter}")
async def version_history(project_id: str, content_type: str, chapter: str,
                          current=Depends(auth.get_current_user)):
    """All versions of one specific piece (e.g. all drafts of chapter 3)."""
    _assert_owns_project(project_id, current["id"])
    if chapter in ("null", "none", "project", "-"):
        rows = db.query_all(
            "SELECT id, phase, chapter_number, content_type, word_count, "
            "critic_verdict, created_at FROM versions "
            "WHERE project_id = %s AND content_type = %s AND chapter_number IS NULL "
            "ORDER BY created_at ASC",
            (project_id, content_type),
        )
    else:
        try:
            ch = int(chapter)
        except ValueError:
            raise HTTPException(400, "Invalid chapter.")
        rows = db.query_all(
            "SELECT id, phase, chapter_number, content_type, word_count, "
            "critic_verdict, created_at FROM versions "
            "WHERE project_id = %s AND content_type = %s AND chapter_number = %s "
            "ORDER BY created_at ASC",
            (project_id, content_type, ch),
        )
    return {"versions": [_row_summary(r) for r in rows]}


@router.get("/detail/{version_id}")
async def version_detail(version_id: str, current=Depends(auth.get_current_user)):
    """Full content of a single version (ownership enforced via user_id)."""
    row = db.query_one(
        "SELECT * FROM versions WHERE id = %s AND user_id = %s",
        (version_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Version not found.")
    import json
    return {
        "id": str(row["id"]),
        "project_id": str(row["project_id"]),
        "phase": row["phase"],
        "chapter_number": row["chapter_number"],
        "content_type": row["content_type"],
        "content": row["content"],
        "word_count": row["word_count"],
        "critic_verdict": row["critic_verdict"],
        "metadata": json.loads(row.get("metadata_json") or "{}"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }
