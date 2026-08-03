"""Version tracking — the key new feature of Open-Write Web.

Every time a pipeline phase produces output, and every time a user manually
saves a chapter, we capture an immutable snapshot into the ``versions`` table.
This lets the writer browse every draft, see the critic reports that triggered
revisions, and compare versions over time.

The main entry point ``capture_phase_versions`` inspects the dict returned by
``orchestrator.advance_phase`` and reads the produced artifact file(s) from the
project directory, then writes one row per artifact.
"""
from __future__ import annotations

import json
import os
from typing import Any

from app import db

# Maps a pipeline phase key to the version ``content_type`` it produces.
PHASE_CONTENT_TYPE = {
    "bible": "bible",
    "voice": "voice",
    "editorial_lock": "editorial_lock",
    "architect": "architect_plan",
    "writer": "chapter_draft",
    "editorial": "critic_editorial",
    "assemble": "assembly",
    "adversarial": "adversarial_report",
    "finalize": "completion_certificate",
}

# Maps a critic_type to its version content_type.
CRITIC_CONTENT_TYPE = {
    "show": "critic_show",
    "voice": "critic_voice",
    "palette": "critic_palette",
    "continuity": "critic_continuity",
    "naturalism": "critic_naturalism",
    "editorial": "critic_editorial",
}


def _word_count(text: str) -> int:
    return len(text.split())


def _read_artifact(project_dir: str, rel: str) -> str | None:
    """Read an artifact file relative to the project directory, safely."""
    if not rel:
        return None
    full = os.path.realpath(os.path.join(project_dir, rel))
    base = os.path.realpath(project_dir)
    if not full.startswith(base):
        return None
    if not os.path.isfile(full):
        return None
    try:
        with open(full, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def record_version(
    project_id: str,
    user_id: str,
    phase: str,
    content_type: str,
    content: str,
    chapter_number: int | None = None,
    critic_verdict: str | None = None,
    metadata: dict | None = None,
) -> dict | None:
    """Insert a single version snapshot. Returns the created row (id, created_at)."""
    return db.execute(
        """
        INSERT INTO versions
          (project_id, user_id, phase, chapter_number, content_type,
           content, word_count, critic_verdict, metadata_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, created_at
        """,
        (
            project_id,
            user_id,
            phase,
            chapter_number,
            content_type,
            content,
            _word_count(content),
            critic_verdict,
            json.dumps(metadata or {}),
        ),
    )


def capture_phase_versions(
    project_id: str,
    user_id: str,
    project_dir: str,
    advance_result: dict[str, Any],
) -> list[str]:
    """Capture version snapshots from an advance_phase result.

    Reads each produced artifact off disk and stores it. Returns a list of
    the content_types that were captured (for logging / response info).
    """
    captured: list[str] = []
    phase = advance_result.get("phase")
    result = advance_result.get("result") or {}
    chapter = result.get("chapter")
    if isinstance(chapter, str) and chapter.isdigit():
        chapter = int(chapter)

    # ── Critics phase: one snapshot per critic ────────────────────────────
    if phase == "critics":
        for crit in result.get("critics", []) or []:
            ctype = crit.get("critic_type") or crit.get("type")
            content_type = CRITIC_CONTENT_TYPE.get(ctype, f"critic_{ctype}")
            content = _read_artifact(project_dir, crit.get("artifact_path", ""))
            if content is None:
                continue
            record_version(
                project_id, user_id, phase, content_type, content,
                chapter_number=chapter,
                critic_verdict=(crit.get("verdict") or None),
                metadata={"critic_type": ctype,
                          "artifact_path": crit.get("artifact_path")},
            )
            captured.append(content_type)
        return captured

    # ── Phases that may produce multiple bible files ──────────────────────
    artifacts = result.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        content_type = PHASE_CONTENT_TYPE.get(phase, phase or "unknown")
        for rel in artifacts:
            content = _read_artifact(project_dir, rel)
            if content is None:
                continue
            record_version(
                project_id, user_id, phase, content_type, content,
                chapter_number=chapter,
                metadata={"artifact_path": rel},
            )
            captured.append(content_type)
        return captured

    # ── Single-artifact phases ────────────────────────────────────────────
    rel = result.get("artifact")
    if rel:
        content_type = PHASE_CONTENT_TYPE.get(phase, phase or "unknown")
        content = _read_artifact(project_dir, rel)
        if content is not None:
            gate = result.get("gate") or {}
            verdict = gate.get("verdict") if isinstance(gate, dict) else None
            record_version(
                project_id, user_id, phase, content_type, content,
                chapter_number=chapter,
                critic_verdict=verdict,
                metadata={"artifact_path": rel,
                          "word_count_reported": result.get("word_count")},
            )
            captured.append(content_type)

    # ── Finalize: capture the completion certificate if present ───────────
    if phase == "finalize":
        gate = result.get("gate") or result.get("finalize_result") or {}
        cert_rel = None
        if isinstance(gate, dict):
            cert_rel = gate.get("certificate") or gate.get("certificate_path")
        for candidate in (cert_rel, "state/COMPLETION_PASS.json",
                          "COMPLETION_PASS.json"):
            if not candidate:
                continue
            content = _read_artifact(project_dir, candidate)
            if content is not None:
                record_version(
                    project_id, user_id, phase, "completion_certificate", content,
                    metadata={"artifact_path": candidate},
                )
                captured.append("completion_certificate")
                break

    return captured
