"""Editorial Review routes — review and revise external work.

Allows users to upload any text and run the Open-Write critic agents on it,
then optionally revise the material through the LLM. Not tied to a pipeline
project — this is for work produced outside Open-Write.
"""
from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth, settings_store

router = APIRouter(prefix="/api/editorial", tags=["editorial"])

# All available critic types with display labels.
CRITIC_OPTIONS = [
    {"id": "show", "label": "Show Don't Tell", "description": "Enforces physical rendering over named emotion"},
    {"id": "voice", "label": "Voice Consistency", "description": "Checks character voice distinctiveness and prose distance"},
    {"id": "palette", "label": "Emotional Palette", "description": "Verifies emotional range is rendered, not named"},
    {"id": "continuity", "label": "Continuity", "description": "Checks state, timeline, and callback consistency"},
    {"id": "naturalism", "label": "Naturalism / AI-Tell", "description": "Hunts for machine prose fingerprints"},
    {"id": "editorial", "label": "Editorial", "description": "Structural and prose editorial pass"},
]


def _resolve_call_model(qualified: str | None):
    from app.ai.providers import resolve
    target = qualified or settings_store.get_default_model()
    try:
        resolved = resolve(target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not resolved.is_configured:
        raise HTTPException(
            status_code=400,
            detail=(f"The '{resolved.label}' provider isn't configured. "
                    f"Add its base URL and API key in Settings."),
        )
    return resolved.api_key, resolved.model_name, resolved.base_url


def _get_critic_prompt(critic_type: str) -> str:
    """Return the system prompt for a critic type."""
    prompts = {
        "show": (
            "You are the SHOW critic. Enforce show-don't-tell. Flag every place "
            "the text TELLS an emotion or state instead of SHOWING it through "
            "physical detail, action, or dialogue. For EVERY finding, cite the "
            "location and quote the exact passage:\n"
            "  Line N: \"<the exact quoted text>\" — <what is wrong and how to fix it>\n\n"
            "Output contract:\n"
            "1. A ## Findings section with at least three located findings.\n"
            "2. A one-paragraph overall assessment.\n"
            "3. End with: VERDICT: PASS  (or ADVANCE or REVISE)\n"
            "PASS = nothing to fix. ADVANCE = minor notes. REVISE = must fix."
        ),
        "voice": (
            "You are the VOICE critic. Check that each character speaks in a "
            "distinct, consistent register and that the narration's prose distance "
            "varies. Flag dialogue that sounds interchangeable between characters, "
            "a character dropping their register without cause, or monotone prose "
            "distance. For EVERY finding cite Line N + quote the exact passage.\n\n"
            "Output contract:\n"
            "1. ## Findings with at least three located findings.\n"
            "2. A one-paragraph overall assessment.\n"
            "3. VERDICT: PASS | ADVANCE | REVISE"
        ),
        "palette": (
            "You are the PALETTE critic. Verify the emotional range is rendered "
            "(not named) and the dominant emotion is earned. Flag the same "
            "emotional register held too long, emotional beats asserted rather "
            "than dramatized, and thin sensory palette. For EVERY finding cite "
            "Line N + quote the exact passage.\n\n"
            "Output contract:\n"
            "1. ## Findings with at least three located findings.\n"
            "2. A one-paragraph overall assessment.\n"
            "3. VERDICT: PASS | ADVANCE | REVISE"
        ),
        "continuity": (
            "You are the CONTINUITY critic. Check internal consistency: character "
            "knowledge, physical state, timeline, and pronoun/identity clarity. "
            "Flag contradictions, timeline impossibilities, and ambiguous references. "
            "For EVERY finding cite Line N + quote the exact passage.\n\n"
            "Output contract:\n"
            "1. ## Findings with at least three located findings.\n"
            "2. A one-paragraph overall assessment.\n"
            "3. VERDICT: PASS | ADVANCE | REVISE"
        ),
        "naturalism": (
            "You are the NATURALISM critic. Hunt for machine-prose fingerprints: "
            "em-dash overuse, triplet closings, uniform sentence rhythm, stock "
            "refrains, generic metaphor. For EVERY finding cite Line N + quote "
            "the exact passage and suggest a concrete replacement.\n\n"
            "Output contract:\n"
            "1. ## Findings with at least three located findings.\n"
            "2. A one-paragraph overall assessment.\n"
            "3. VERDICT: PASS | ADVANCE | REVISE"
        ),
        "editorial": (
            "You are the EDITORIAL critic. Assess the text as an editor would: "
            "does it earn its word count, does it open and close with intention, "
            "is the pacing right. Flag scenes that don't earn their place, weak "
            "openings/endings, pacing problems, and beats that should be cut. "
            "For EVERY finding cite Line N + quote the exact passage.\n\n"
            "Output contract:\n"
            "1. ## Findings with at least three located findings.\n"
            "2. A one-paragraph overall assessment.\n"
            "3. VERDICT: PASS | ADVANCE | REVISE"
        ),
    }
    return prompts.get(critic_type, prompts["editorial"])


# ── Request / Response models ────────────────────────────────────────────────

class ReviewRequest(BaseModel):
    content: str
    critics: list[str] | None = None  # None = run all six


class ReviewResponse(BaseModel):
    results: dict[str, dict]  # critic_type -> { report, verdict, word_count }
    model_used: str


class ReviseRequest(BaseModel):
    content: str
    feedback: str = ""  # combined critic feedback from the review
    instructions: str = ""  # user's additional revision instructions
    rounds: int = 1  # number of revision passes


class ReviseResponse(BaseModel):
    revised_content: str
    model_used: str
    round_number: int


class CriticsListResponse(BaseModel):
    critics: list[dict]


# ── Routes ──────────────────────────────────────────────────────────────────

@router.get("/critics", response_model=CriticsListResponse)
async def list_critics():
    """Return the available critic types."""
    return CriticsListResponse(critics=CRITIC_OPTIONS)


@router.post("/review", response_model=ReviewResponse)
async def review_content(req: ReviewRequest,
                         current=Depends(auth.get_current_user)):
    """Run selected critics on user-provided content."""
    if not req.content.strip():
        raise HTTPException(400, "Content cannot be empty.")

    critics_to_run = req.critics or [c["id"] for c in CRITIC_OPTIONS]
    # Validate critic types
    valid_ids = {c["id"] for c in CRITIC_OPTIONS}
    for cid in critics_to_run:
        if cid not in valid_ids:
            raise HTTPException(400, f"Unknown critic type: {cid}")

    api_key, model_name, base_url = _resolve_call_model(None)

    # Number the lines so critics can cite "Line N".
    numbered_lines = []
    for i, line in enumerate(req.content.split("\n"), 1):
        numbered_lines.append(f"{i}: {line}")
    numbered_content = "\n".join(numbered_lines)

    results = {}
    from app.ai.openrouter import run_chat
    for critic_type in critics_to_run:
        system = _get_critic_prompt(critic_type)
        user = (
            f"chapter_hash: editorial-review\n\n"
            f"--- TEXT ---\n{numbered_content}\n--- END TEXT ---\n\n"
            f"Review this text now. Begin your report with 'chapter_hash: editorial-review', "
            f"include a ## Findings section with at least three located findings "
            f"(Line N + quoted span), then VERDICT."
        )
        try:
            reply = await run_chat(
                api_key=api_key, model_id=model_name, base_url=base_url,
                system_prompt=system, messages=[{"role": "user", "content": user}],
                temperature=0.4,
            )
            # Extract verdict
            import re
            verdict_match = re.search(r'(?i)VERDICT:\s*(PASS|ADVANCE|REVISE)', reply)
            verdict = verdict_match.group(1).upper() if verdict_match else "UNKNOWN"
            results[critic_type] = {
                "report": reply,
                "verdict": verdict,
                "word_count": len(reply.split()),
            }
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            results[critic_type] = {
                "report": f"Error running {critic_type} critic: {exc}",
                "verdict": "ERROR",
                "word_count": 0,
            }

    return ReviewResponse(results=results, model_used=model_name)


@router.post("/revise", response_model=ReviseResponse)
async def revise_content(req: ReviseRequest,
                         current=Depends(auth.get_current_user)):
    """Revise content based on critic feedback. Can be called multiple times."""
    if not req.content.strip():
        raise HTTPException(400, "Content cannot be empty.")

    api_key, model_name, base_url = _resolve_call_model(None)

    system = (
        "You are a SKILLED REVISER. You receive a piece of writing and editorial "
        "feedback. Your job is to revise the text to address every finding while "
        "preserving what works. Do NOT start from scratch — revise the existing "
        "text to resolve the specific issues flagged. Maintain the author's voice "
        "and intent. Output ONLY the revised text, no commentary."
    )

    user_parts = [f"--- ORIGINAL TEXT ---\n{req.content}\n--- END TEXT ---"]
    if req.feedback:
        user_parts.append(f"--- EDITORIAL FEEDBACK ---\n{req.feedback}\n--- END FEEDBACK ---")
    if req.instructions:
        user_parts.append(f"--- ADDITIONAL INSTRUCTIONS ---\n{req.instructions}\n--- END INSTRUCTIONS ---")
    user_parts.append("Revise the text now. Output ONLY the revised text.")

    from app.ai.openrouter import run_chat

    current_text = req.content
    last_reply = current_text
    for round_num in range(1, req.rounds + 1):
        if round_num > 1:
            # On subsequent rounds, feed the previous revision back with the same feedback
            user_parts[0] = f"--- TEXT TO REVISE ---\n{current_text}\n--- END TEXT ---"
        user = "\n\n".join(user_parts)
        try:
            reply = await run_chat(
                api_key=api_key, model_id=model_name, base_url=base_url,
                system_prompt=system, messages=[{"role": "user", "content": user}],
                temperature=0.5,
            )
            last_reply = reply.strip()
            current_text = last_reply
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            if round_num == 1:
                raise HTTPException(502, f"Model provider error: {exc}")
            break  # Return what we have so far

    return ReviseResponse(
        revised_content=last_reply,
        model_used=model_name,
        round_number=req.rounds,
    )
