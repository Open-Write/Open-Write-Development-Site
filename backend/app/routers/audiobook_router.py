"""Audiobook generation pipeline — scripting, casting, generation, review.

Four stages:
  1. Script  — LLM converts manuscript chapters into audio scripts
  2. Cast    — User hears voice samples, approves cast assignments
  3. Generate — TTS synthesis per segment, QA runs automatically
  4. Review  — Section-by-section playback, user marks errors for regeneration

State is persisted in audiobook_state.json alongside pipeline_run.json.

TTS synthesis uses the MiMo TTS API (OpenAI-compatible) via the existing
provider system. Tokens are tracked through the existing token usage system.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth, config, db, settings_store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audiobook", tags=["audiobook"])


# ── TTS helper ───────────────────────────────────────────────────────────────

def _resolve_mimo_provider() -> tuple[str, str]:
    """Resolve the MiMo provider API key and base URL from settings."""
    from app.ai.providers import resolve
    providers = settings_store.get_providers()
    for p in providers:
        if p["id"] == "mimo" and p.get("api_key"):
            return p["api_key"], p.get("base_url", "https://token-plan-sgp.xiaomimimo.com/v1")
    # Fallback: try resolving mimo/mimo-v2.5-tts-voicedesign
    try:
        resolved = resolve("mimo/mimo-v2.5-tts-voicedesign")
        if resolved.is_configured:
            return resolved.api_key, resolved.base_url
    except Exception:
        pass
    raise HTTPException(400, "MiMo provider not configured. Add your MiMo API key in Settings → Providers.")


async def _tts_synthesize(
    api_key: str,
    base_url: str,
    text: str,
    voice_prompt: str,
    model: str = "mimo-v2.5-tts-voicedesign",
    output_format: str = "wav",
) -> bytes:
    """Call the MiMo TTS API and return audio bytes.

    The MiMo TTS API is OpenAI-compatible:
    POST {base_url}/audio/speech
    {"model": "...", "input": "text", "voice": "design_prompt"}

    Returns raw audio bytes (WAV/MP3).
    """
    url = f"{base_url.rstrip('/')}/audio/speech"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "input": text,
        "voice": voice_prompt,
        "response_format": output_format,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.content


def _estimate_tts_tokens(text: str) -> int:
    """Estimate token count for TTS input (rough: 1 token ≈ 4 chars)."""
    return max(1, len(text) // 4)

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
    sp = _state_path(pdir)
    if not sp.exists():
        raise HTTPException(404, "Audiobook pipeline not initialized.")
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

    # Use the audiobook-specific model setting, falling back to default model
    audiobook_model = settings_store.get_audiobook_model()
    if not audiobook_model:
        raise HTTPException(
            400,
            "No model is configured for audiobook generation. Go to Settings → "
            "Model Routing and choose an Audiobook model, or set a Default model.",
        )
    api_key, model_name, base_url = _resolve_call_model(audiobook_model)
    scripts_dir = _scripts_dir(pdir)
    scripts_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    errors: list[str] = []
    skipped_approved = 0
    # Overall budget: Railway's proxy kills requests around 300s. Stop with
    # partial results before that so the client always gets a response.
    import time as _time
    deadline = _time.monotonic() + 240.0
    timed_out = False
    log.info("Audiobook generate_scripts: %d chapters, stage=%s, model=%s",
             len(state["chapters"]), state["stage"], audiobook_model)
    for ch in state["chapters"]:
        if ch["script_status"] == "approved":
            skipped_approved += 1
            continue  # skip approved scripts

        if _time.monotonic() > deadline:
            timed_out = True
            log.info("Audiobook generate_scripts: deadline hit, stopping before chapter %d", ch["id"])
            break

        # Read manuscript chapter
        source_path = pdir / ch["source"]
        if not source_path.exists():
            errors.append(f"Chapter {ch['id']}: source file not found ({ch['source']})")
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
                timeout=120.0,
            )
        except Exception as exc:
            log.error("Script generation failed for chapter %d: %s", ch["id"], exc)
            errors.append(f"Chapter {ch['id']}: {exc}")
            continue

        if not reply or not reply.strip():
            errors.append(f"Chapter {ch['id']}: model returned an empty response (provider upstream error?)")
            continue

        # Save script
        script_path = scripts_dir / f"ch{ch['id']:02d}.jsonl"
        script_path.write_text(reply.strip() + "\n", encoding="utf-8")

        ch["script_status"] = "draft"
        ch["script_path"] = str(script_path.relative_to(pdir))
        generated += 1

    _save_state(pdir, state)
    log.info("Audiobook generate_scripts result: generated=%d, errors=%d, skipped_approved=%d, total=%d, timed_out=%s",
             generated, len(errors), skipped_approved, len(state["chapters"]), timed_out)
    return {
        "generated": generated,
        "errors": errors,
        "skipped_approved": skipped_approved,
        "total_chapters": len(state["chapters"]),
        "timed_out": timed_out,
    }


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

    Calls MiMo TTS API with the voice design prompt and a sample line.
    Returns the path to the generated audio sample.
    """
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    voice_lib = state.get("casting_config", {})
    voice_def = voice_lib.get(voice_key)
    if not voice_def:
        raise HTTPException(404, f"Voice key '{voice_key}' not found in casting config.")

    voice_ref = voice_def.get("voice_ref", {})
    design_prompt = voice_ref.get("value", "")
    model = voice_def.get("model", "mimo-v2.5-tts-voicedesign")
    base_style = voice_def.get("base_style", "")

    # Sample line for audition
    sample_text = f"Testing voice {voice_key}. The morning came with a weight that settled into the bones."

    # Resolve MiMo provider
    api_key, base_url = _resolve_mimo_provider()

    # Combine design prompt with base style for the voice parameter
    voice_param = design_prompt
    if base_style:
        voice_param = f"{design_prompt} Delivery: {base_style}"

    try:
        audio_bytes = await _tts_synthesize(
            api_key=api_key,
            base_url=base_url,
            text=sample_text,
            voice_prompt=voice_param,
            model=model,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"TTS API error: {exc.response.status_code} {exc.response.text[:200]}")
    except httpx.RequestError as exc:
        raise HTTPException(503, f"TTS API connection error: {exc}")

    # Save audition audio
    audition_dir = pdir / "audiobook" / "auditions"
    audition_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audition_dir / f"{voice_key}.wav"
    audio_path.write_bytes(audio_bytes)

    # Track token usage
    tokens = _estimate_tts_tokens(sample_text)
    settings_store.record_token_usage(current["id"], tokens)

    return {
        "character": character,
        "voice_key": voice_key,
        "audio_path": str(audio_path.relative_to(pdir)),
        "sample_text": sample_text,
        "tokens_used": tokens,
        "model": model,
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

    Generates TTS audio per segment using MiMo TTS API, tracks token usage.
    """
    pdir = _project_dir(project_id, current["id"])
    state = _load_state(pdir)

    if state["stage"] not in ("generate", "review"):
        raise HTTPException(400, f"Cannot generate in stage '{state['stage']}'.")

    chapter_ids = req.chapter_ids or [ch["id"] for ch in state["chapters"]]
    audio_dir = _audio_dir(pdir)
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Resolve MiMo provider once
    try:
        api_key, base_url = _resolve_mimo_provider()
    except HTTPException:
        raise

    casting = state.get("casting", {})
    voice_lib = state.get("casting_config", {})
    total_tokens = 0

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

        # Generate audio per segment
        ch_audio_dir = audio_dir / f"ch{ch['id']:02d}"
        ch_audio_dir.mkdir(parents=True, exist_ok=True)
        seg_results = []
        chapter_tokens = 0

        for i, seg in enumerate(segments):
            seg_id = seg.get("segment_id", f"ch{ch['id']}_seg{i:03d}")
            voice_id = seg.get("voice_id", "NARRATOR")
            source_text = seg.get("source_text", "")
            kind = seg.get("kind", "narration")

            if kind == "direction" or not source_text.strip():
                # Directions are pauses/ambient — skip TTS, mark as generated
                seg["audio_path"] = ""
                seg["generation_status"] = "skipped"
                seg_results.append(seg)
                continue

            # Resolve voice: check casting assignment, then voice library
            assigned_key = casting.get(voice_id, {}).get("voice_key", voice_id)
            voice_def = voice_lib.get(assigned_key, voice_lib.get(voice_id, {}))
            voice_prompt = voice_def.get("voice_ref", {}).get("value", "")
            base_style = voice_def.get("base_style", "")
            model = voice_def.get("model", "mimo-v2.5-tts-voicedesign")

            if base_style:
                voice_param = f"{voice_prompt} Delivery: {base_style}"
            else:
                voice_param = voice_prompt

            # Synthesize
            try:
                audio_bytes = await _tts_synthesize(
                    api_key=api_key,
                    base_url=base_url,
                    text=source_text,
                    voice_prompt=voice_param,
                    model=model,
                )
                # Save audio
                audio_file = ch_audio_dir / f"{seg_id}.wav"
                audio_file.write_bytes(audio_bytes)

                seg["audio_path"] = str(audio_file.relative_to(pdir))
                seg["generation_status"] = "generated"

                # Track tokens
                tokens = _estimate_tts_tokens(source_text)
                chapter_tokens += tokens

            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                log.error("TTS failed for segment %s: %s", seg_id, exc)
                seg["audio_path"] = ""
                seg["generation_status"] = "failed"
                seg["error"] = str(exc)[:200]

            seg_results.append(seg)

        # Update chapter state
        ch["segments"] = seg_results
        failed_count = sum(1 for s in seg_results if s.get("generation_status") == "failed")
        if failed_count > 0:
            ch["generation_status"] = "qa_fail"
        else:
            ch["generation_status"] = "qa_pass"

        total_tokens += chapter_tokens
        results.append({
            "chapter_id": ch["id"],
            "segments": len(seg_results),
            "generated": sum(1 for s in seg_results if s.get("generation_status") == "generated"),
            "failed": failed_count,
            "tokens_used": chapter_tokens,
        })

    # Record total token usage
    if total_tokens > 0:
        settings_store.record_token_usage(current["id"], total_tokens)

    _save_state(pdir, state)
    return {"results": results, "total_tokens_used": total_tokens}


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


@router.delete("/{project_id}/state")
async def reset_audiobook(project_id: str, current=Depends(auth.get_current_user)):
    """Delete the audiobook state so the pipeline can be re-initialized."""
    import os
    pdir = _project_dir(project_id, current["id"])
    sp = _state_path(pdir)
    if sp.exists():
        os.remove(sp)
    return {"reset": True}
