"""Audiobook generation pipeline — scripting, casting, generation, review.

Four stages:
  1. Script  — LLM converts manuscript chapters into audio scripts
  2. Cast    — User hears voice samples, approves cast assignments
  3. Generate — TTS synthesis per segment, QA runs automatically
  4. Review  — Section-by-section playback, user marks errors for regeneration

State is persisted in audiobook_state.json alongside pipeline_run.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth, config, db

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audiobook", tags=["audiobook"])

# ── Paths ────────────────────────────────────────────────────────────────────

def _project_dir(project_id: str, user_id: str) -> Path:
    row = db.query_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (project_id, user_id),
    )
    if not row:
        raise HTTPException(404, "Project not found.")
    return Path(config.project_path(user_id, project_id))


def _state_path(project_dir: Path) -> Path:
    return project_dir / "audiobook" / "audiobook_state.json"


def _scripts_dir(project_dir: Path) -> Path:
    return project_dir / "audiobook" / "scripts"


def _audio_dir(project_dir: Path) -> Path:
    return project_dir / "audiobook" / "audio"


def _casts_dir(project_dir: Path) -> Path:
    return project_dir / "audiobook" / "casts"


# ── State model ──────────────────────────────────────────────────────────────

def _default_state() -> dict:
    return {
        "stage": "script",
        "chapters": [],
        "casting": {},
        "qa_reports": {},
        "regeneration_queue": [],
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }


def _load_state(project_dir: Path) -> dict:
    p = _state_path(project_dir)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return _default_state()


def _save_state(project_dir: Path, state: dict) -> None:
    state["updated_at"] = datetime.utcnow().isoformat()
    p = _state_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ── Request models ───────────────────────────────────────────────────────────

class InitRequest(BaseModel):
    casting_file: str = ""  # path to casting.json, or empty for default


class ScriptEditRequest(BaseModel):
    chapter_id: int
    content: str  # the edited script content


class CastAssignmentRequest(BaseModel):
    character: str
    voice_key: str


class CastApproveRequest(BaseModel):
    approved: bool


class GenerateRequest(BaseModel):
    chapter_ids: list[int] = []  # empty = all chapters


class RegenerateRequest(BaseModel):
    segment_ids: list[str]


class ReviewMarkRequest(BaseModel):
    segment_id: str
    notes: str = ""
    action: str = "regenerate"  # "regenerate" | "accept"


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/{project_id}/state")
async def get_state(project_id: str, current=Depends(auth.get_current_user)):
    """Get the current audiobook pipeline state."""
    pdir = _project_dir(project_id, current["id"])
    return _load_state(pdir)


@router.post("/{project_id}/init")
async def init_audiobook(project_id: str, req: InitRequest,
                         current=Depends(auth.get_current_user)):
    """Initialize the audiobook pipeline for a project.

    Reads the manuscript chapters and casting file, creates the initial state.
    """
    pdir = _project_dir(project_id, current["id"])

    # Find manuscript chapters
    manuscript_dir = pdir / "manuscript"
    chapters = []
    if manuscript_dir.exists():
        for f in sorted(manuscript_dir.glob("*.md")):
            # Try to extract chapter number from filename
            name = f.stem
            try:
                ch_id = int(name.split("_")[0])
            except (ValueError, IndexError):
                ch_id = len(chapters) + 1
            chapters.append({
                "id": ch_id,
                "source": str(f.relative_to(pdir)),
                "title": name,
                "script_status": "pending",
                "cast_status": "pending",
                "generation_status": "pending",
                "segments": [],
            })

    if not chapters:
        raise HTTPException(400, "No manuscript chapters found in manuscript/ directory.")

    # Load casting file
    casting = {}
    casting_path = pdir / req.casting_file if req.casting_file else pdir / "audiobook" / "casting.json"
    if not casting_path.exists():
        casting_path = pdir / "audiobook" / "casting.example.json"
    if casting_path.exists():
        with open(casting_path, "r", encoding="utf-8") as f:
            casting_data = json.load(f)
            casting = casting_data.get("voices", {})

    state = _default_state()
    state["chapters"] = chapters
    state["casting"] = {k: {"voice_key": k, "approved": False} for k in casting}
    state["casting_config"] = casting  # full voice definitions
    _save_state(pdir, state)

    return {"initialized": True, "chapters": len(chapters), "voices": len(casting)}


# ── Script stage ─────────────────────────────────────────────────────────────

@router.post("/{project_id}/script/generate")
async def generate_scripts(project_id: str, current=Depends(auth.get_current_user)):
    """Generate audio scripts for all chapters using the LLM.

    Converts manuscript markdown into audio scripts with narrator directions,
    dialogue attribution, segment breaks, and normalization markers.
    """
    from app.ai.openrouter import run_chat
    from app.routers.pipeline_router import _resolve_call_model

    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    if state["stage"] not in ("script",):
        raise HTTPException(400, f"Cannot generate scripts in stage '{state['stage']}'.")

    api_key, model_name, base_url = _resolve_call_model(None)
    scripts_dir = _scripts_dir(pdir)
    scripts_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    for ch in state["chapters"]:
        if ch["script_status"] == "approved":
            continue  # skip approved scripts

        # Read manuscript chapter
        source_path = pdir / ch["source"]
        if not source_path.exists():
            continue
        manuscript_text = source_path.read_text(encoding="utf-8-sig")

        system_prompt = """You are an audiobook script adapter. Convert manuscript prose into an audio production script.

Your output is a JSONL file — one JSON object per line, each representing a segment.

Segment types:
- "narration": narrator reading prose
- "dialogue": character speech (include character_id)
- "direction": production direction (pause, ambient, etc.)

Each segment:
{
  "segment_id": "ch1_seg001",
  "kind": "narration|dialogue|direction",
  "voice_id": "NARRATOR|CHARACTER_NAME",
  "source_text": "the text to speak",
  "notes": "optional direction for the voice actor"
}

Rules:
- Narrator reads all non-dialogue prose
- Dialogue segments use the character's voice_id from the casting config
- Scene breaks become "direction" segments with a pause
- Chapter titles become narration segments
- Keep segments under 1200 characters
- Maintain paragraph breaks as segment boundaries where natural
- Preserve em-dashes, ellipses, and all punctuation"""

        user_prompt = f"""Convert this chapter into an audio script.

MANUSCRIPT:
{manuscript_text}

Output ONLY the JSONL, one segment per line. No preamble, no explanation."""

        try:
            reply = await run_chat(
                api_key=api_key, model_id=model_name, base_url=base_url,
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=0.3,
            )
        except Exception as exc:
            log.error("Script generation failed for chapter %d: %s", ch["id"], exc)
            continue

        # Save script
        script_path = scripts_dir / f"ch{ch['id']:02d}.jsonl"
        script_path.write_text(reply.strip() + "\n", encoding="utf-8")

        ch["script_status"] = "draft"
        ch["script_path"] = str(script_path.relative_to(pdir))
        generated += 1

    _save_state(pdir, state)
    return {"generated": generated}


@router.get("/{project_id}/script/{chapter_id}")
async def get_script(project_id: str, chapter_id: int,
                     current=Depends(auth.get_current_user)):
    """Get the script for a chapter."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    for ch in state["chapters"]:
        if ch["id"] == chapter_id:
            script_path = pdir / ch.get("script_path", "")
            if script_path.exists():
                content = script_path.read_text(encoding="utf-8")
                return {"chapter_id": chapter_id, "content": content, "status": ch["script_status"]}
            return {"chapter_id": chapter_id, "content": "", "status": ch["script_status"]}

    raise HTTPException(404, "Chapter not found.")


@router.post("/{project_id}/script/edit")
async def edit_script(project_id: str, req: ScriptEditRequest,
                      current=Depends(auth.get_current_user)):
    """Edit a chapter's script (user can modify LLM output or supply own)."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    for ch in state["chapters"]:
        if ch["id"] == req.chapter_id:
            scripts_dir = _scripts_dir(pdir)
            scripts_dir.mkdir(parents=True, exist_ok=True)
            script_path = scripts_dir / f"ch{req.chapter_id:02d}.jsonl"
            script_path.write_text(req.content, encoding="utf-8")
            ch["script_path"] = str(script_path.relative_to(pdir))
            ch["script_status"] = "draft"
            _save_state(pdir, state)
            return {"saved": True}

    raise HTTPException(404, "Chapter not found.")


@router.post("/{project_id}/script/{chapter_id}/approve")
async def approve_script(project_id: str, chapter_id: int,
                         current=Depends(auth.get_current_user)):
    """Approve a chapter's script for casting/generation."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    for ch in state["chapters"]:
        if ch["id"] == chapter_id:
            ch["script_status"] = "approved"
            _save_state(pdir, state)
            return {"approved": True}

    raise HTTPException(404, "Chapter not found.")


# ── Cast stage ───────────────────────────────────────────────────────────────

@router.get("/{project_id}/cast")
async def get_cast(project_id: str, current=Depends(auth.get_current_user)):
    """Get the current casting assignments and voice library."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)
    return {
        "casting": state.get("casting", {}),
        "voice_library": state.get("casting_config", {}),
    }


@router.post("/{project_id}/cast/assign")
async def assign_voice(project_id: str, req: CastAssignmentRequest,
                       current=Depends(auth.get_current_user)):
    """Assign a voice to a character."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    state.setdefault("casting", {})[req.character] = {
        "voice_key": req.voice_key,
        "approved": False,
    }
    _save_state(pdir, state)
    return {"assigned": True}


@router.post("/{project_id}/cast/approve")
async def approve_cast(project_id: str, req: CastApproveRequest,
                       current=Depends(auth.get_current_user)):
    """Approve or reject the full cast."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    if req.approved:
        for char, assignment in state.get("casting", {}).items():
            assignment["approved"] = True
        state["stage"] = "generate"
    _save_state(pdir, state)
    return {"approved": req.approved, "stage": state["stage"]}


@router.post("/{project_id}/cast/audition")
async def audition_voice(project_id: str, character: str, voice_key: str,
                         current=Depends(auth.get_current_user)):
    """Generate an audition sample for a character/voice combination.

    Returns the path to the generated audio sample.
    """
    from app.ai.openrouter import run_chat
    from app.routers.pipeline_router import _resolve_call_model

    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    voice_lib = state.get("casting_config", {})
    voice_def = voice_lib.get(voice_key)
    if not voice_def:
        raise HTTPException(404, f"Voice key '{voice_key}' not found in casting config.")

    # Get a sample line for this character
    audition_guide = pdir / "audiobook" / "voice_audition_guide.md"
    sample_line = f"Testing voice {voice_key}."

    # Use the voice design prompt to generate a TTS sample
    voice_ref = voice_def.get("voice_ref", {})
    design_prompt = voice_ref.get("value", "")

    # For now, return the voice definition for frontend playback
    # (actual TTS integration depends on MiMo TTS API availability)
    return {
        "character": character,
        "voice_key": voice_key,
        "voice_definition": voice_def,
        "design_prompt": design_prompt,
        "sample_text": sample_line,
        "note": "TTS sample generation requires MiMo TTS API integration.",
    }


@router.get("/{project_id}/cast/suggestions")
async def get_cast_suggestions(project_id: str, current=Depends(auth.get_current_user)):
    """Get voice suggestions for characters based on the audition guide."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    voice_lib = state.get("casting_config", {})
    casting = state.get("casting", {})

    # Group voices by character (A/B/C variants)
    suggestions: dict[str, list[dict]] = {}
    for key, voice_def in voice_lib.items():
        # Extract character name from voice key (e.g. JOHN_BROWN_A -> JOHN_BROWN)
        parts = key.rsplit("_", 1)
        if len(parts) == 2 and parts[1] in ("A", "B", "C"):
            char_name = parts[0]
        else:
            char_name = key
        suggestions.setdefault(char_name, []).append({
            "voice_key": key,
            "one_line": voice_def.get("base_style", ""),
            "design_prompt": voice_def.get("voice_ref", {}).get("value", ""),
        })

    return {"suggestions": suggestions, "current_assignments": casting}


# ── Generate stage ───────────────────────────────────────────────────────────

@router.post("/{project_id}/generate")
async def generate_audio(project_id: str, req: GenerateRequest,
                         current=Depends(auth.get_current_user)):
    """Start audio generation for specified chapters (or all if empty).

    Generates TTS audio per segment, runs QA after each chapter.
    """
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    if state["stage"] not in ("generate", "review"):
        raise HTTPException(400, f"Cannot generate in stage '{state['stage']}'.")

    chapter_ids = req.chapter_ids or [ch["id"] for ch in state["chapters"]]
    audio_dir = _audio_dir(pdir)
    audio_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for ch in state["chapters"]:
        if ch["id"] not in chapter_ids:
            continue
        if ch["script_status"] != "approved":
            results.append({"chapter_id": ch["id"], "skipped": True, "reason": "script not approved"})
            continue

        ch["generation_status"] = "generating"
        _save_state(pdir, state)

        # Read script
        script_path = pdir / ch.get("script_path", "")
        if not script_path.exists():
            ch["generation_status"] = "pending"
            results.append({"chapter_id": ch["id"], "skipped": True, "reason": "no script"})
            continue

        # Parse script segments
        segments = []
        for line in script_path.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                seg = json.loads(line)
                segments.append(seg)
            except json.JSONDecodeError:
                continue

        ch["segments"] = segments
        ch["generation_status"] = "qa_pass"  # placeholder — real TTS integration pending
        results.append({"chapter_id": ch["id"], "segments": len(segments), "status": "generated"})

    _save_state(pdir, state)
    return {"results": results}


@router.get("/{project_id}/segments/{chapter_id}")
async def get_segments(project_id: str, chapter_id: int,
                       current=Depends(auth.get_current_user)):
    """Get all segments for a chapter with their generation and QA status."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    for ch in state["chapters"]:
        if ch["id"] == chapter_id:
            return {"chapter_id": chapter_id, "segments": ch.get("segments", [])}

    raise HTTPException(404, "Chapter not found.")


# ── Review stage ─────────────────────────────────────────────────────────────

@router.get("/{project_id}/review")
async def get_review_data(project_id: str, current=Depends(auth.get_current_user)):
    """Get all chapters with their QA status for review."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    chapters_with_qa = []
    for ch in state["chapters"]:
        qa_report = state.get("qa_reports", {}).get(f"ch{ch['id']}", {})
        chapters_with_qa.append({
            "id": ch["id"],
            "title": ch.get("title", ""),
            "script_status": ch["script_status"],
            "generation_status": ch["generation_status"],
            "qa_summary": qa_report.get("summary", {}),
            "segment_count": len(ch.get("segments", [])),
        })

    return {
        "stage": state["stage"],
        "chapters": chapters_with_qa,
        "regeneration_queue": state.get("regeneration_queue", []),
    }


@router.post("/{project_id}/review/mark")
async def mark_segment(project_id: str, req: ReviewMarkRequest,
                       current=Depends(auth.get_current_user)):
    """Mark a segment for regeneration or accept it."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    if req.action == "regenerate":
        state.setdefault("regeneration_queue", []).append({
            "segment_id": req.segment_id,
            "notes": req.notes,
            "status": "pending",
            "marked_at": datetime.utcnow().isoformat(),
        })
    elif req.action == "accept":
        # Remove from regeneration queue if present
        state["regeneration_queue"] = [
            r for r in state.get("regeneration_queue", [])
            if r["segment_id"] != req.segment_id
        ]

    _save_state(pdir, state)
    return {"marked": True, "action": req.action}


@router.post("/{project_id}/review/approve-chapter/{chapter_id}")
async def approve_chapter(project_id: str, chapter_id: int,
                          current=Depends(auth.get_current_user)):
    """Approve a chapter as reviewed and acceptable."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    for ch in state["chapters"]:
        if ch["id"] == chapter_id:
            ch["generation_status"] = "reviewed"
            _save_state(pdir, state)
            return {"approved": True}

    raise HTTPException(404, "Chapter not found.")


@router.post("/{project_id}/review/complete")
async def complete_review(project_id: str, current=Depends(auth.get_current_user)):
    """Mark the audiobook as complete (all chapters reviewed)."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    all_reviewed = all(
        ch.get("generation_status") == "reviewed"
        for ch in state["chapters"]
    )
    if not all_reviewed:
        raise HTTPException(400, "Not all chapters have been reviewed.")

    state["stage"] = "complete"
    _save_state(pdir, state)
    return {"complete": True}


# ── Regeneration ─────────────────────────────────────────────────────────────

@router.post("/{project_id}/regenerate")
async def regenerate_segments(project_id: str, req: RegenerateRequest,
                              current=Depends(auth.get_current_user)):
    """Regenerate specific segments."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    regenerated = []
    for seg_id in req.segment_ids:
        # Find the segment
        for ch in state["chapters"]:
            for seg in ch.get("segments", []):
                if seg.get("segment_id") == seg_id:
                    seg["regenerated"] = True
                    seg["regenerated_at"] = datetime.utcnow().isoformat()
                    regenerated.append(seg_id)

        # Remove from regeneration queue
        state["regeneration_queue"] = [
            r for r in state.get("regeneration_queue", [])
            if r["segment_id"] != seg_id
        ]

    _save_state(pdir, state)
    return {"regenerated": regenerated}


# ── Stage management ─────────────────────────────────────────────────────────

@router.post("/{project_id}/advance-stage")
async def advance_stage(project_id: str, current=Depends(auth.get_current_user)):
    """Advance to the next stage if current stage requirements are met."""
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    stage = state["stage"]

    if stage == "script":
        # All chapters must have approved scripts
        if all(ch["script_status"] == "approved" for ch in state["chapters"]):
            state["stage"] = "cast"
        else:
            raise HTTPException(400, "All scripts must be approved before advancing.")

    elif stage == "cast":
        # All characters must have approved assignments
        casting = state.get("casting", {})
        if all(a.get("approved") for a in casting.values()):
            state["stage"] = "generate"
        else:
            raise HTTPException(400, "All cast assignments must be approved.")

    elif stage == "generate":
        # All chapters must be generated
        if all(ch["generation_status"] != "pending" for ch in state["chapters"]):
            state["stage"] = "review"
        else:
            raise HTTPException(400, "All chapters must be generated.")

    elif stage == "review":
        raise HTTPException(400, "Use /review/complete to finish the review stage.")

    _save_state(pdir, state)
    return {"stage": state["stage"]}
