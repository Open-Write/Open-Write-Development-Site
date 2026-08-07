"""Version history routes — browse captured snapshots, restore, and diff.

- GET  /api/versions/{project_id}                       -> grouped list (no content)
- GET  /api/versions/{project_id}/history/{ct}/{chapter} -> all versions of one piece
- GET  /api/versions/detail/{version_id}                -> full content of one version
- POST /api/versions/{project_id}/restore/{version_id}  -> restore a prior version
- GET  /api/versions/diff/{version_id_a}/{version_id_b} -> diff between two versions
"""
from __future__ import annotations

import difflib
import json
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth, config, db, versions

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


# ── Restore ──────────────────────────────────────────────────────────────────

def _safe_join(project: str, rel: str) -> str:
    full = os.path.realpath(os.path.join(project, rel))
    if not full.startswith(project + os.sep) and full != project:
        raise HTTPException(400, "Invalid path.")
    return full


class RestoreResponse(BaseModel):
    restored: bool
    path: str
    word_count: int
    new_version_id: str


@router.post("/{project_id}/restore/{version_id}", response_model=RestoreResponse)
async def restore_version(project_id: str, version_id: str,
                          current=Depends(auth.get_current_user)):
    """Restore a prior version's content back to its chapter path.

    Writes the version's content to disk (same mechanism as save_chapter) and
    captures a NEW user_edit version so the restore itself is tracked in history
    rather than silently overwriting.
    """
    _assert_owns_project(project_id, current["id"])

    # Fetch the version to restore.
    row = db.query_one(
        "SELECT * FROM versions WHERE id = %s AND user_id = %s AND project_id = %s",
        (version_id, current["id"], project_id),
    )
    if not row:
        raise HTTPException(404, "Version not found.")

    content = row["content"]
    if not content:
        raise HTTPException(400, "Version has no content to restore.")

    # Determine the target file path from metadata.
    meta = json.loads(row.get("metadata_json") or "{}")
    artifact_path = meta.get("artifact_path")
    if not artifact_path:
        raise HTTPException(
            400,
            "This version has no associated file path and cannot be restored automatically. "
            "It may be a project-level artifact (bible, voice spec) that isn't editable from the Write tab."
        )

    # Resolve the project directory and write the content.
    pdir = config.project_path(current["id"], project_id)
    if not pdir.is_dir():
        raise HTTPException(404, "Project directory not found.")
    project = os.path.realpath(str(pdir))
    full = _safe_join(project, artifact_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)

    # Capture a new version marking this as a restore action.
    ch = row["chapter_number"]
    new_row = versions.record_version(
        project_id, current["id"], phase="restore",
        content_type="user_edit", content=content,
        chapter_number=ch,
        metadata={
            "artifact_path": artifact_path,
            "source": "restore",
            "restored_from_version": version_id,
        },
    )

    return RestoreResponse(
        restored=True,
        path=artifact_path,
        word_count=len(content.split()),
        new_version_id=str(new_row["id"]) if new_row else "",
    )


# ── Diff ─────────────────────────────────────────────────────────────────────

class DiffLine(BaseModel):
    type: str  # "equal", "insert", "delete", "replace"
    old_line: str | None = None
    new_line: str | None = None


class DiffResponse(BaseModel):
    version_a_id: str
    version_b_id: str
    content_type: str
    chapter_number: int | None
    lines: list[DiffLine]
    stats: dict  # {insertions, deletions, unchanged}


@router.get("/diff/{version_id_a}/{version_id_b}", response_model=DiffResponse)
async def version_diff(version_id_a: str, version_id_b: str,
                       current=Depends(auth.get_current_user)):
    """Return a line-by-line diff between two versions.

    Uses Python's difflib (SequenceMatcher) for a clean, human-readable diff
    without requiring an external diff binary.
    """
    # Fetch both versions, enforcing ownership.
    row_a = db.query_one(
        "SELECT * FROM versions WHERE id = %s AND user_id = %s",
        (version_id_a, current["id"]),
    )
    row_b = db.query_one(
        "SELECT * FROM versions WHERE id = %s AND user_id = %s",
        (version_id_b, current["id"]),
    )
    if not row_a:
        raise HTTPException(404, f"Version A not found: {version_id_a}")
    if not row_b:
        raise HTTPException(404, f"Version B not found: {version_id_b}")

    # They should be for the same content type.
    if row_a["content_type"] != row_b["content_type"]:
        raise HTTPException(
            400,
            f"Cannot diff different content types: '{row_a['content_type']}' vs '{row_b['content_type']}'."
        )

    lines_a = (row_a["content"] or "").splitlines(keepends=True)
    lines_b = (row_b["content"] or "").splitlines(keepends=True)

    matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
    result: list[DiffLine] = []
    insertions = 0
    deletions = 0
    unchanged = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for line in lines_a[i1:i2]:
                result.append(DiffLine(type="equal", old_line=line.rstrip("\n"), new_line=line.rstrip("\n")))
                unchanged += 1
        elif tag == "replace":
            # Pair up old/new lines; extras get their own entries.
            old_lines = lines_a[i1:i2]
            new_lines = lines_b[j1:j2]
            pairs = min(len(old_lines), len(new_lines))
            for k in range(pairs):
                result.append(DiffLine(
                    type="replace",
                    old_line=old_lines[k].rstrip("\n"),
                    new_line=new_lines[k].rstrip("\n"),
                ))
                insertions += 1
                deletions += 1
            for line in old_lines[pairs:]:
                result.append(DiffLine(type="delete", old_line=line.rstrip("\n")))
                deletions += 1
            for line in new_lines[pairs:]:
                result.append(DiffLine(type="insert", new_line=line.rstrip("\n")))
                insertions += 1
        elif tag == "insert":
            for line in lines_b[j1:j2]:
                result.append(DiffLine(type="insert", new_line=line.rstrip("\n")))
                insertions += 1
        elif tag == "delete":
            for line in lines_a[i1:i2]:
                result.append(DiffLine(type="delete", old_line=line.rstrip("\n")))
                deletions += 1

    return DiffResponse(
        version_a_id=version_id_a,
        version_b_id=version_id_b,
        content_type=row_a["content_type"],
        chapter_number=row_a["chapter_number"],
        lines=result,
        stats={"insertions": insertions, "deletions": deletions, "unchanged": unchanged},
    )
