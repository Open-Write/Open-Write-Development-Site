"""Editorial Review routes — review, revise, and track external work.

Allows users to upload any text, run critics and adversarial readers on it,
revise through the LLM with version tracking, and generate supporting materials.
Not tied to a pipeline project — this is for work produced outside Open-Write.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import auth, db, settings_store

router = APIRouter(prefix="/api/editorial", tags=["editorial"])

# ── Critic / Reader options ──────────────────────────────────────────────────

CRITIC_OPTIONS = [
    {"id": "show", "label": "Show Don't Tell", "description": "Enforces physical rendering over named emotion", "category": "critic"},
    {"id": "voice", "label": "Voice Consistency", "description": "Checks character voice distinctiveness and prose distance", "category": "critic"},
    {"id": "palette", "label": "Emotional Palette", "description": "Verifies emotional range is rendered, not named", "category": "critic"},
    {"id": "continuity", "label": "Continuity", "description": "Checks state, timeline, and callback consistency", "category": "critic"},
    {"id": "naturalism", "label": "Naturalism / AI-Tell", "description": "Hunts for machine prose fingerprints", "category": "critic"},
    {"id": "editorial", "label": "Editorial", "description": "Structural and prose editorial pass", "category": "critic"},
    {"id": "reader_screenplay", "label": "Script Reader (Lara Marsh)", "description": "Professional studio coverage — Pass/Consider/Recommend", "category": "reader"},
    {"id": "reader_fiction", "label": "Literary Editor (Marisol Reyes)", "description": "Acquisition-level literary fiction coverage", "category": "reader"},
    {"id": "editor", "label": "Developmental Editor (Alex Vane)", "description": "Direct, efficient editorial notes with action items", "category": "reader"},
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


# ── Critic system prompts ────────────────────────────────────────────────────

def _get_critic_prompt(critic_type: str) -> str:
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
            "3. End with: VERDICT: PASS  (or ADVANCE or REVISE)"
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
            "is the pacing right. For EVERY finding cite Line N + quote the exact passage.\n\n"
            "Output contract:\n"
            "1. ## Findings with at least three located findings.\n"
            "2. A one-paragraph overall assessment.\n"
            "3. VERDICT: PASS | ADVANCE | REVISE"
        ),
    }
    return prompts.get(critic_type, prompts["editorial"])


# ── Adversarial reader prompts ───────────────────────────────────────────────

def _get_reader_prompt(reader_type: str) -> str:
    if reader_type == "reader_screenplay":
        return _READER_SCREENPLAY
    if reader_type == "reader_fiction":
        return _READER_FICTION
    if reader_type == "editor":
        return _EDITOR_ALEX_VANE
    return _READER_FICTION


_READER_SCREENPLAY = """\
You are Lara Marsh, a professional contest and studio script reader. Fourteen years in the role. Coverage credits across two majors, three management companies, and the Black List. You read forty to sixty scripts a month. You are tired in the specific way only readers are tired — not from working too much, but from reading the same opening pages ten thousand times and watching most of them fail in the same ways.

You are not cruel. You are not generous. You are calibrated.

You do not hate writers. You hate generic writing. You will give a clean Pass to a script that is competent but unremarkable, because a Consider sends a real human reader downstream — someone with a limited day and a job to keep — to spend ninety minutes on those pages on your recommendation. You owe that reader honesty. You owe the writer honesty. You owe yourself the truth of what is on the page.

You have not spoken to the writer. You are reading what arrived in your inbox this morning. The only context you bring is fourteen years of professional reading.

What you read
You read only what the user pastes or attaches. Script pages. That is all.
You do NOT read or request: Production bibles, treatments, outlines, or pitch decks. Character breakdowns, scene plans, or writer's notes. Anything explaining what the script is "trying to do." Reviews, prior coverage, or context about the writer.
If the user provides any of those alongside the script, ignore them.

Your scale
For full scripts, three ratings. They mean specific things.
PASS — "I would not push this up." Roughly 80% of scripts.
CONSIDER — "There is something here. The writer can write." Roughly 15%.
RECOMMEND — "This is rare. The writing is operating at a professional level on every dimension." Roughly 3-5%.
You do not give half-grades. You commit to a rating.

For partial drafts, use:
WOULD STOP — "I would stop reading here."
WOULD CONTINUE — "I would keep reading, with reservations."
ENGAGED — "I want to know what happens next."

What you read for
The page-1 hook. Voice. Character voice differentiation. Subtext. What the page can show. Restraint. Center of gravity. What the scene is doing. Earned momentum. The page-30 question. Tics that give writers away.

How you write coverage

COVERAGE — [PROJECT TITLE]
Pages read: [N pages, M scenes — Act/scope]
Reader: Lara Marsh
Date: [date]

VERDICT: [PASS / CONSIDER / RECOMMEND]
(For partial drafts: WOULD STOP / WOULD CONTINUE / ENGAGED)

WHAT THE PAGES ARE
[One paragraph. What kind of script this appears to be, what its center of gravity feels like.]

WHAT WORKS
[One to three paragraphs. Specific lines, specific scenes, specific choices. Cite page numbers.]

WHAT DOESN'T WORK
[One to four paragraphs. Specific lines, specific scenes, specific choices. Cite page numbers. Order by severity.]

PAGE-LEVEL ISSUES
[A short bulleted list. For each: page reference, the line or beat, the issue. Eight lines maximum.]

WOULD A READER KEEP READING
[One paragraph. Honest assessment.]

You do not compare against an idealized version. You read what is on the page.
You do not give credit for ambition. Ambition without execution is a Pass.
You do not soften your verdict because the writer is trying.
You do not give the writer notes for revision. That is someone else's job. You write coverage.

When you receive the user's script pages, read what they sent and produce coverage. If they have not yet sent pages, ask them to paste or attach the script.
"""


_READER_FICTION = """\
You are Marisol Reyes, a senior editor at an independent literary press. Twelve years in the role. Acquired and edited fifteen-twenty literary novels, including two that have won or been shortlisted for major prizes. You read roughly forty submissions a month.

You are not cruel. You are not generous. You are calibrated.

You do not hate writers. You hate generic literary writing — prose that signals literary fiction without doing the work of literary fiction.

Your scale
For full manuscripts: REJECTION (85%), READ WITH EDITORIAL (12%), ACQUISITION RECOMMENDATION (2-3%).
For partial drafts: WOULD SET DOWN, READING WITH RESERVATIONS, ENGAGED.

What you read for
The opening pages. Voice on the sentence level. The relationship between scene and summary. Interiority that does work. Subtext. The texture of close-up moments. Sentence rhythm. The avoidance of generic literary loftiness. The believability of the world. Earned momentum. Prose tics that mark untrained or AI-influenced writing.

Prose tics to flag:
- Not just X but Y construction
- In a way that Z construction
- Hedge words: somewhat, perhaps, in some sense, almost as if
- Em-dashes used to elaborate when a period would do
- Sentences that begin with There was/There is
- She felt that, he thought that — telling-not-rendering
- Adverbs ending in -ly modifying verbs that already carry meaning
- Something between X and Y to avoid committing
- Three-element parallel lists as default rhythm
- The way X moves through Y metaphorical constructions

How you write coverage

COVERAGE — [TITLE]
Pages read: [N pages, scope]
Reader: Marisol Reyes
Date: [date]

VERDICT: [REJECTION / READ WITH EDITORIAL / ACQUISITION RECOMMENDATION]

WHAT THE PAGES ARE
[One paragraph. What kind of literary novel this appears to be, its center of gravity, what tradition it speaks to.]

WHAT WORKS
[One to three paragraphs. Specific sentences, specific scenes. Cite page or chapter references.]

WHAT DOESN'T WORK
[One to four paragraphs. Specific sentences, specific scenes. Cite page or chapter references. Order by severity.]

LINE-LEVEL ISSUES
[A short bulleted list. For each: page reference, the line or beat, the issue. Eight lines maximum.]

WOULD A READER KEEP READING
[One paragraph. Honest assessment.]

You do not give credit for ambition. You do not soften your verdict. You do not pad. You do not write in bullet points except for the line-level issues list. You do not give the writer notes for revision.

Calibrated against: Stoner, The Sea The Sea, Gilead, A Visit from the Goon Squad, Lincoln in the Bardo, Klara and the Sun, Trust, The Overstory, Outline, The Days of Abandonment, Drive Your Plow Over the Bones of the Dead.

When you receive the user's manuscript pages, read what they sent and produce coverage.
"""


_EDITOR_ALEX_VANE = """\
You are Alex Vane. You've been a professional editor for eighteen years — six at a major New York publishing house, twelve as a high-end freelance developmental editor. You charge $450 an hour. You have a waiting list.

You are not mean. You are not generous. You are efficient.

Your reputation is simple: you tell writers what is actually on the page, not what they hoped was on the page. You do not soften problems to spare feelings — feelings are not the work. The work is the work.

Your guiding principles:
1. Candor is kindness. Protecting a writer's feelings is stealing their time.
2. Don't explain what works unless it matters.
3. Do explain what doesn't work, specifically. Vague negatives are useless. Specific negatives are actionable.
4. The problem is rarely what the writer thinks it is. Most manuscripts die because the prose is generic, the characters have no subtext, or the opening pages don't earn the next ones.
5. Efficiency over thoroughness. Identify the systemic issue, give 2-3 clear examples, move on.
6. No hedging, no softening. Write "cut this scene" not "you might want to think about cutting this scene."
7. You do not praise work that is not ready.

Your note structure:

MANUSCRIPT NOTES

Overall assessment: Two to three sentences. Verdict first. Then the core problem in concrete terms.

What's not working (priority order): Numbered list. Each issue gets 1-3 paragraphs. Lead with the most damaging problem. For each issue: name it, show it (cite page/line), explain why it fails, state what the writer must do.

What's working (briefly): One short paragraph. Do not pad.

Action items: A bulleted list of specific revisions. Not suggestions. Instructions. "Cut pages 12-18." "Rewrite Chapter 4 from the husband's POV or cut it."

One-sentence bottom line: What the writer should actually do with this draft.

Your tone: Direct. Professional. Not cruel but not warm. Plain language. You do not say "the prose struggles to achieve the requisite specificity" — you say "the prose is generic on pages 3-10."

What you do not do:
- You do not read story bibles, outlines, or author's notes.
- You do not ask the writer what they "meant" to do.
- You do not give line edits unless the manuscript is close.
- You do not apologize for candor.

When the user pastes manuscript pages, respond with your notes following this structure and tone.
"""


# ── Request / Response models ────────────────────────────────────────────────

class CreateReviewRequest(BaseModel):
    title: str = "Untitled"
    content: str
    format: str = "prose"  # prose | screenplay | tv


class UpdateReviewRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    format: str | None = None


class RunReviewRequest(BaseModel):
    critics: list[str] | None = None  # None = run all critics (not readers)


class RunReaderRequest(BaseModel):
    reader_type: str  # reader_screenplay | reader_fiction | editor


class ReviseRequest(BaseModel):
    instructions: str = ""
    rounds: int = 1


class GenerateMaterialsRequest(BaseModel):
    material_types: list[str] | None = None  # None = generate all


# ── Helper functions ─────────────────────────────────────────────────────────

def _number_content(content: str) -> str:
    """Add line numbers to content for critic citation."""
    lines = content.split("\n")
    return "\n".join(f"{i}: {line}" for i, line in enumerate(lines, 1))


def _run_model(system: str, user: str, api_key: str, model_name: str, base_url: str) -> str:
    """Synchronous wrapper — callers must be async."""
    import asyncio
    from app.ai.openrouter import run_chat
    return asyncio.get_event_loop().run_until_complete(
        run_chat(api_key=api_key, model_id=model_name, base_url=base_url,
                 system_prompt=system, messages=[{"role": "user", "content": user}],
                 temperature=0.4)
    )


async def _run_model_async(system: str, user: str, api_key: str, model_name: str, base_url: str, step: str = "") -> str:
    from app.ai.openrouter import run_chat
    return await run_chat(
        api_key=api_key, model_id=model_name, base_url=base_url,
        system_prompt=system, messages=[{"role": "user", "content": user}],
        temperature=0.4, pipeline_step=step,
    )


def _extract_verdict(text: str) -> str:
    """Extract verdict from a report."""
    m = re.search(r'(?i)VERDICT:\s*(PASS|ADVANCE|REVISE|RECOMMEND|CONSIDER|REJECTION|READ WITH EDITORIAL|ACQUISITION RECOMMENDATION|WOULD STOP|WOULD CONTINUE|ENGAGED|WOULD SET DOWN|READING WITH RESERVATIONS)', text)
    return m.group(1).upper() if m else "UNKNOWN"


# ── Routes ──────────────────────────────────────────────────────────────────

@router.get("/critics")
async def list_critics():
    return {"critics": CRITIC_OPTIONS}


@router.post("/reviews")
async def create_review(req: CreateReviewRequest,
                        current=Depends(auth.get_current_user)):
    """Create a new editorial review — saves the uploaded content."""
    if not req.content.strip():
        raise HTTPException(400, "Content cannot be empty.")
    row = db.execute(
        "INSERT INTO editorial_reviews (user_id, title, original_content, current_content, format) "
        "VALUES (%s, %s, %s, %s, %s) "
        "RETURNING id, title, format, created_at, updated_at",
        (current["id"], req.title.strip() or "Untitled",
         req.content.strip(), req.content.strip(), req.format),
    )
    review_id = str(row["id"])
    # Create initial version
    db.execute(
        "INSERT INTO editorial_review_versions (review_id, version_number, content, feedback, instructions) "
        "VALUES (%s, 1, %s, '', 'Original upload')",
        (review_id, req.content.strip()),
    )
    return {
        "id": review_id,
        "title": row["title"],
        "format": row["format"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


@router.get("/reviews")
async def list_reviews(current=Depends(auth.get_current_user)):
    """List all editorial reviews for the current user."""
    rows = db.query_all(
        "SELECT id, title, format, created_at, updated_at "
        "FROM editorial_reviews WHERE user_id = %s ORDER BY updated_at DESC",
        (current["id"],),
    )
    return {
        "reviews": [
            {
                "id": str(r["id"]),
                "title": r["title"],
                "format": r["format"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
            }
            for r in rows
        ]
    }


@router.get("/reviews/{review_id}")
async def get_review(review_id: str, current=Depends(auth.get_current_user)):
    """Get a specific review with its current content, reports, and version count."""
    row = db.query_one(
        "SELECT * FROM editorial_reviews WHERE id = %s AND user_id = %s",
        (review_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Review not found.")

    reports = db.query_all(
        "SELECT report_type, report, verdict, created_at "
        "FROM editorial_review_reports WHERE review_id = %s ORDER BY created_at DESC",
        (review_id,),
    )
    versions = db.query_all(
        "SELECT version_number, feedback, instructions, created_at "
        "FROM editorial_review_versions WHERE review_id = %s ORDER BY version_number DESC",
        (review_id,),
    )

    return {
        "id": str(row["id"]),
        "title": row["title"],
        "format": row["format"],
        "original_content": row["original_content"],
        "current_content": row["current_content"],
        "supporting_materials": json.loads(row["supporting_materials"]) if row.get("supporting_materials") else {},
        "reports": [
            {
                "report_type": r["report_type"],
                "report": r["report"],
                "verdict": r["verdict"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in reports
        ],
        "versions": [
            {
                "version_number": v["version_number"],
                "feedback": v["feedback"][:200] + "…" if v.get("feedback") and len(v["feedback"]) > 200 else v.get("feedback", ""),
                "instructions": v["instructions"],
                "created_at": v["created_at"].isoformat() if v.get("created_at") else None,
            }
            for v in versions
        ],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


@router.put("/reviews/{review_id}")
async def update_review(review_id: str, req: UpdateReviewRequest,
                        current=Depends(auth.get_current_user)):
    """Update review content (e.g. after manual edits)."""
    row = db.query_one(
        "SELECT id FROM editorial_reviews WHERE id = %s AND user_id = %s",
        (review_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Review not found.")

    updates = []
    params = []
    if req.title is not None:
        updates.append("title = %s")
        params.append(req.title.strip())
    if req.content is not None:
        updates.append("current_content = %s")
        params.append(req.content.strip())
    if req.format is not None:
        updates.append("format = %s")
        params.append(req.format)
    if updates:
        updates.append("updated_at = NOW()")
        params.append(review_id)
        db.execute(
            f"UPDATE editorial_reviews SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )
    return {"updated": True}


@router.delete("/reviews/{review_id}")
async def delete_review(review_id: str, current=Depends(auth.get_current_user)):
    """Delete a review and all its versions and reports."""
    row = db.query_one(
        "SELECT id FROM editorial_reviews WHERE id = %s AND user_id = %s",
        (review_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Review not found.")
    db.execute("DELETE FROM editorial_reviews WHERE id = %s", (review_id,))
    return {"deleted": True}


@router.post("/reviews/{review_id}/review")
async def run_critics(review_id: str, req: RunReviewRequest,
                      current=Depends(auth.get_current_user)):
    """Run selected critics on a saved review."""
    row = db.query_one(
        "SELECT id, current_content FROM editorial_reviews WHERE id = %s AND user_id = %s",
        (review_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Review not found.")

    content = row["current_content"]
    critics_to_run = req.critics or [c["id"] for c in CRITIC_OPTIONS if c["category"] == "critic"]

    api_key, model_name, base_url = _resolve_call_model(None)
    numbered = _number_content(content)

    results = {}
    for critic_type in critics_to_run:
        system = _get_critic_prompt(critic_type)
        user = (
            f"chapter_hash: editorial-review\n\n"
            f"--- TEXT ---\n{numbered}\n--- END TEXT ---\n\n"
            f"Review this text now. Begin with 'chapter_hash: editorial-review', "
            f"include ## Findings with located findings, then VERDICT."
        )
        try:
            reply = await _run_model_async(system, user, api_key, model_name, base_url)
            verdict = _extract_verdict(reply)
            # Save report to DB
            db.execute(
                "INSERT INTO editorial_review_reports (review_id, report_type, report, verdict) "
                "VALUES (%s, %s, %s, %s)",
                (review_id, critic_type, reply, verdict),
            )
            results[critic_type] = {"report": reply, "verdict": verdict}
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            results[critic_type] = {"report": f"Error: {exc}", "verdict": "ERROR"}

    # Update timestamp
    db.execute("UPDATE editorial_reviews SET updated_at = NOW() WHERE id = %s", (review_id,))
    return {"results": results, "model_used": model_name}


@router.post("/reviews/{review_id}/reader")
async def run_reader(review_id: str, req: RunReaderRequest,
                     current=Depends(auth.get_current_user)):
    """Run an adversarial reader on a saved review."""
    if req.reader_type not in ("reader_screenplay", "reader_fiction", "editor"):
        raise HTTPException(400, f"Unknown reader type: {req.reader_type}")

    row = db.query_one(
        "SELECT id, current_content, format FROM editorial_reviews WHERE id = %s AND user_id = %s",
        (review_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Review not found.")

    api_key, model_name, base_url = _resolve_call_model(None)
    system = _get_reader_prompt(req.reader_type)
    user = f"--- PAGES ---\n{row['current_content']}\n--- END PAGES ---"

    try:
        reply = await _run_model_async(system, user, api_key, model_name, base_url)
        verdict = _extract_verdict(reply)
        db.execute(
            "INSERT INTO editorial_review_reports (review_id, report_type, report, verdict) "
            "VALUES (%s, %s, %s, %s)",
            (review_id, req.reader_type, reply, verdict),
        )
        db.execute("UPDATE editorial_reviews SET updated_at = NOW() WHERE id = %s", (review_id,))
        return {"report": reply, "verdict": verdict, "model_used": model_name}
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise HTTPException(502, f"Model provider error: {exc}")


@router.post("/reviews/{review_id}/materials")
async def generate_materials(review_id: str, req: GenerateMaterialsRequest,
                             current=Depends(auth.get_current_user)):
    """Generate supporting materials (bible, profiles, format rules) for the work."""
    row = db.query_one(
        "SELECT id, current_content, format, supporting_materials "
        "FROM editorial_reviews WHERE id = %s AND user_id = %s",
        (review_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Review not found.")

    api_key, model_name, base_url = _resolve_call_model(None)
    content = row["current_content"]
    fmt = row["format"]
    existing = json.loads(row["supporting_materials"]) if row.get("supporting_materials") else {}

    material_types = req.material_types or ["bible", "profiles", "format_rules"]
    generated = {}

    for mat_type in material_types:
        if mat_type == "bible":
            system = (
                "You are a literary analyst. Read the provided text and produce a concise "
                "story bible with these sections:\n"
                "## Concept — one paragraph summary of what this story is about\n"
                "## Characters — list each character with: name, role, key traits, arc\n"
                "## World — setting, time period, social context\n"
                "## Themes — the 2-3 central themes\n"
                "## Structure — how the story is organized (acts, sections, timeline)\n"
                "Be specific. Cite details from the text. Do not invent."
            )
        elif mat_type == "profiles":
            system = (
                "You are a literary analyst. Read the provided text and produce a character "
                "profile for EACH named character. For each character, write:\n"
                "## [Character Name]\n"
                "- Role in the story\n"
                "- Key traits and mannerisms\n"
                "- Voice patterns (how they speak)\n"
                "- Arc (how they change)\n"
                "- Key relationships\n"
                "- Defining moments in the text\n"
                "Be specific. Cite lines from the text."
            )
        elif mat_type == "format_rules":
            if fmt == "screenplay":
                system = (
                    "You are a screenplay format specialist. Read the provided screenplay and "
                    "extract the format rules being used:\n"
                    "- Slug line conventions (INT./EXT. style)\n"
                    "- Character name formatting\n"
                    "- Parenthetical usage\n"
                    "- Action line style\n"
                    "- Transition usage\n"
                    "- Any deviations from standard Fountain format\n"
                    "Produce a concise format rules document."
                )
            elif fmt == "tv":
                system = (
                    "You are a TV script format specialist. Read the provided teleplay and "
                    "extract the format rules being used:\n"
                    "- Act break structure (cold open, acts, tag)\n"
                    "- Slug line conventions\n"
                    "- Character name formatting\n"
                    "- Scene direction style\n"
                    "- Any deviations from standard TV Fountain format\n"
                    "Produce a concise format rules document."
                )
            else:
                system = (
                    "You are a prose style analyst. Read the provided text and extract the "
                    "style rules being used:\n"
                    "- Narrative POV and distance\n"
                    "- Sentence rhythm patterns\n"
                    "- Dialogue formatting\n"
                    "- Description conventions\n"
                    "- Chapter/section structure\n"
                    "Produce a concise style guide for this work."
                )
        else:
            continue

        user = f"--- TEXT ---\n{content[:16000]}\n--- END TEXT ---"
        try:
            reply = await _run_model_async(system, user, api_key, model_name, base_url)
            generated[mat_type] = reply
        except (httpx.HTTPStatusError, httpx.RequestError):
            generated[mat_type] = f"Error generating {mat_type}."

    # Merge with existing materials and save
    updated_materials = {**existing, **generated}
    db.execute(
        "UPDATE editorial_reviews SET supporting_materials = %s, updated_at = NOW() WHERE id = %s",
        (json.dumps(updated_materials), review_id),
    )
    return {"materials": generated, "model_used": model_name}


@router.post("/reviews/{review_id}/revise")
async def revise_content(review_id: str, req: ReviseRequest,
                         current=Depends(auth.get_current_user)):
    """Revise content with version tracking. Each round creates a new version."""
    row = db.query_one(
        "SELECT id, current_content, format, supporting_materials "
        "FROM editorial_reviews WHERE id = %s AND user_id = %s",
        (review_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Review not found.")

    # Get the latest version number
    ver_row = db.query_one(
        "SELECT MAX(version_number) as max_ver FROM editorial_review_versions WHERE review_id = %s",
        (review_id,),
    )
    current_version = (ver_row or {}).get("max_ver") or 1

    # Collect all critic feedback
    reports = db.query_all(
        "SELECT report_type, report, verdict FROM editorial_review_reports WHERE review_id = %s",
        (review_id,),
    )
    feedback_parts = []
    for r in reports:
        if r["verdict"] in ("REVISE", "ADVANCE"):
            feedback_parts.append(f"--- {r['report_type'].upper()} ---\n{r['report']}\n--- END ---")
    combined_feedback = "\n\n".join(feedback_parts)

    # Get supporting materials for context
    materials = json.loads(row["supporting_materials"]) if row.get("supporting_materials") else {}
    materials_context = ""
    if materials:
        for key, value in materials.items():
            materials_context += f"\n\n--- {key.upper()} ---\n{value[:4000]}\n--- END ---"

    api_key, model_name, base_url = _resolve_call_model(None)

    system = (
        "You are a SKILLED REVISER. You receive a piece of writing, editorial feedback, "
        "and supporting materials. Your job is to revise the text to address every finding "
        "while preserving what works. Do NOT start from scratch — revise the existing "
        "text to resolve the specific issues flagged. Maintain the author's voice and intent. "
        "Output ONLY the revised text, no commentary."
    )

    current_text = row["current_content"]
    last_reply = current_text

    for round_num in range(1, req.rounds + 1):
        user_parts = [f"--- TEXT TO REVISE ---\n{current_text}\n--- END TEXT ---"]
        if combined_feedback:
            user_parts.append(f"--- EDITORIAL FEEDBACK ---\n{combined_feedback}\n--- END FEEDBACK ---")
        if materials_context:
            user_parts.append(f"--- SUPPORTING MATERIALS ---\n{materials_context}\n--- END MATERIALS ---")
        if req.instructions:
            user_parts.append(f"--- ADDITIONAL INSTRUCTIONS ---\n{req.instructions}\n--- END INSTRUCTIONS ---")
        user_parts.append("Revise the text now. Output ONLY the revised text.")

        try:
            reply = await _run_model_async(system, "\n\n".join(user_parts), api_key, model_name, base_url)
            last_reply = reply.strip()
            current_text = last_reply
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            if round_num == 1:
                raise HTTPException(502, f"Model provider error: {exc}")
            break

    # Save new version
    new_version = current_version + 1
    db.execute(
        "INSERT INTO editorial_review_versions (review_id, version_number, content, feedback, instructions) "
        "VALUES (%s, %s, %s, %s, %s)",
        (review_id, new_version, last_reply, combined_feedback[:5000], req.instructions),
    )
    # Update current content
    db.execute(
        "UPDATE editorial_reviews SET current_content = %s, updated_at = NOW() WHERE id = %s",
        (last_reply, review_id),
    )

    return {
        "revised_content": last_reply,
        "model_used": model_name,
        "version_number": new_version,
    }


@router.get("/reviews/{review_id}/versions")
async def list_versions(review_id: str, current=Depends(auth.get_current_user)):
    """Get version history for a review."""
    row = db.query_one(
        "SELECT id FROM editorial_reviews WHERE id = %s AND user_id = %s",
        (review_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Review not found.")

    versions = db.query_all(
        "SELECT version_number, content, feedback, instructions, created_at "
        "FROM editorial_review_versions WHERE review_id = %s ORDER BY version_number DESC",
        (review_id,),
    )
    return {
        "versions": [
            {
                "version_number": v["version_number"],
                "content": v["content"],
                "feedback": v["feedback"],
                "instructions": v["instructions"],
                "created_at": v["created_at"].isoformat() if v.get("created_at") else None,
            }
            for v in versions
        ]
    }


@router.get("/reviews/{review_id}/versions/{version_number}")
async def get_version(review_id: str, version_number: int,
                      current=Depends(auth.get_current_user)):
    """Get a specific version's content."""
    row = db.query_one(
        "SELECT id FROM editorial_reviews WHERE id = %s AND user_id = %s",
        (review_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Review not found.")

    ver = db.query_one(
        "SELECT content, feedback, instructions, created_at "
        "FROM editorial_review_versions WHERE review_id = %s AND version_number = %s",
        (review_id, version_number),
    )
    if not ver:
        raise HTTPException(404, "Version not found.")

    return {
        "version_number": version_number,
        "content": ver["content"],
        "feedback": ver["feedback"],
        "instructions": ver["instructions"],
        "created_at": ver["created_at"].isoformat() if ver.get("created_at") else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOM ADVERSARIAL READER PERSONAS
# ══════════════════════════════════════════════════════════════════════════════


# ── Request models ───────────────────────────────────────────────────────────

class CompilePersonaRequest(BaseModel):
    description: str
    genre: str = ""
    audience: str = ""
    draft_stage: str = ""
    rubric: dict | None = None


class SavePersonaRequest(BaseModel):
    persona: dict  # the full persona JSON spec


class RunPersonaRequest(BaseModel):
    persona_id: str
    rubric: dict | None = None  # optional override rubric at execution time
    severity: int | None = None  # optional severity override


class RunMultiplePersonasRequest(BaseModel):
    persona_ids: list[str]
    rubric: dict | None = None


# ── Persona CRUD ─────────────────────────────────────────────────────────────

@router.get("/personas")
async def list_personas(current=Depends(auth.get_current_user)):
    """List all personas for the current user plus built-in personas."""
    from app.ai.persona import BUILTIN_PERSONAS

    rows = db.query_all(
        "SELECT id, persona_json, is_builtin, created_at, updated_at "
        "FROM editorial_personas WHERE user_id = %s ORDER BY updated_at DESC",
        (current["id"],),
    )
    user_personas = []
    for r in rows:
        spec = json.loads(r["persona_json"])
        user_personas.append({
            "id": str(r["id"]),
            "persona_id": spec.get("persona_id", ""),
            "name": spec.get("name", ""),
            "one_line": spec.get("one_line", ""),
            "severity": spec.get("severity", 3),
            "is_builtin": r["is_builtin"],
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        })

    builtin = []
    for p in BUILTIN_PERSONAS:
        builtin.append({
            "id": p["persona_id"],
            "persona_id": p["persona_id"],
            "name": p["name"],
            "one_line": p["one_line"],
            "severity": p["severity"],
            "is_builtin": True,
            "created_at": None,
        })

    return {"personas": builtin + user_personas}


@router.get("/personas/{persona_db_id}")
async def get_persona(persona_db_id: str, current=Depends(auth.get_current_user)):
    """Get a full persona spec by database ID or persona_id slug."""
    from app.ai.persona import BUILTIN_PERSONAS

    # Check built-in first (matched by persona_id slug)
    for p in BUILTIN_PERSONAS:
        if p["persona_id"] == persona_db_id:
            return {"persona": p, "is_builtin": True}

    # Check user's personas — try UUID first, then persona_id slug
    row = db.query_one(
        "SELECT id, persona_json, is_builtin, created_at, updated_at "
        "FROM editorial_personas WHERE id = %s AND user_id = %s",
        (persona_db_id, current["id"]),
    )
    if not row:
        # Try matching by persona_id inside the JSON (slug lookup)
        rows = db.query_all(
            "SELECT id, persona_json, is_builtin, created_at, updated_at "
            "FROM editorial_personas WHERE user_id = %s",
            (current["id"],),
        )
        for r in rows:
            spec = json.loads(r["persona_json"])
            if spec.get("persona_id") == persona_db_id:
                row = r
                break

    if not row:
        raise HTTPException(404, "Persona not found.")
    spec = json.loads(row["persona_json"])
    return {
        "persona": spec,
        "is_builtin": row["is_builtin"],
        "db_id": str(row["id"]),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


@router.post("/personas")
async def save_persona(req: SavePersonaRequest,
                       current=Depends(auth.get_current_user)):
    """Save a persona spec to the user's library."""
    from app.ai.persona import PersonaSpec

    try:
        spec = PersonaSpec(**req.persona)
    except Exception as exc:
        raise HTTPException(400, f"Invalid persona spec: {exc}")

    row = db.execute(
        "INSERT INTO editorial_personas (user_id, persona_json, is_builtin) "
        "VALUES (%s, %s, FALSE) RETURNING id, created_at",
        (current["id"], spec.model_dump_json()),
    )
    return {
        "id": str(row["id"]),
        "persona_id": spec.persona_id,
        "name": spec.name,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


@router.put("/personas/{persona_db_id}")
async def update_persona(persona_db_id: str, req: SavePersonaRequest,
                         current=Depends(auth.get_current_user)):
    """Update an existing persona."""
    from app.ai.persona import PersonaSpec

    row = db.query_one(
        "SELECT id FROM editorial_personas WHERE id = %s AND user_id = %s",
        (persona_db_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Persona not found.")

    try:
        spec = PersonaSpec(**req.persona)
    except Exception as exc:
        raise HTTPException(400, f"Invalid persona spec: {exc}")

    db.execute(
        "UPDATE editorial_personas SET persona_json = %s, updated_at = NOW() WHERE id = %s",
        (spec.model_dump_json(), persona_db_id),
    )
    return {"updated": True, "persona_id": spec.persona_id, "name": spec.name}


@router.delete("/personas/{persona_db_id}")
async def delete_persona(persona_db_id: str, current=Depends(auth.get_current_user)):
    """Delete a persona from the user's library."""
    row = db.query_one(
        "SELECT id FROM editorial_personas WHERE id = %s AND user_id = %s",
        (persona_db_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Persona not found.")
    db.execute("DELETE FROM editorial_personas WHERE id = %s", (persona_db_id,))
    return {"deleted": True}


@router.post("/personas/import")
async def import_persona(body: dict, current=Depends(auth.get_current_user)):
    """Import a persona from JSON (export/share workflow)."""
    from app.ai.persona import PersonaSpec

    try:
        spec = PersonaSpec(**body)
    except Exception as exc:
        raise HTTPException(400, f"Invalid persona spec: {exc}")

    row = db.execute(
        "INSERT INTO editorial_personas (user_id, persona_json, is_builtin) "
        "VALUES (%s, %s, FALSE) RETURNING id, created_at",
        (current["id"], spec.model_dump_json()),
    )
    return {"id": str(row["id"]), "name": spec.name}


# ── Compiler ─────────────────────────────────────────────────────────────────

@router.post("/compile-persona")
async def compile_persona(req: CompilePersonaRequest,
                          current=Depends(auth.get_current_user)):
    """Compile a freeform description into a structured persona spec.

    The compiler produces a draft persona spec that the user can review and
    edit before saving. It infers out_of_scope even when unstated.
    """
    from app.ai.persona import (
        COMPILER_SYSTEM_PROMPT, CompileResult, PersonaSpec,
        check_rubric_scope_conflict,
    )

    api_key, model_name, base_url = _resolve_call_model(None)

    # Build the user message with context fields.
    user_parts = [f"USER DESCRIPTION: {req.description}"]
    if req.genre:
        user_parts.append(f"GENRE: {req.genre}")
    if req.audience:
        user_parts.append(f"AUDIENCE: {req.audience}")
    if req.draft_stage:
        user_parts.append(f"DRAFT STAGE: {req.draft_stage}")
    if req.rubric:
        user_parts.append(f"USER RUBRIC: {json.dumps(req.rubric)}")
    user_message = "\n\n".join(user_parts)

    # Call the compiler with retry on schema violation.
    max_attempts = 2
    last_error = ""
    raw_response = ""

    for attempt in range(max_attempts):
        try:
            raw_response = await _run_model_async(
                COMPILER_SYSTEM_PROMPT, user_message,
                api_key, model_name, base_url, step="persona-compiler",
            )
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            raise HTTPException(502, f"Model provider error: {exc}")

        # Extract JSON from the response (handle markdown fences).
        json_str = raw_response.strip()
        if json_str.startswith("```"):
            json_str = re.sub(r"^```(?:json)?\s*", "", json_str)
            json_str = re.sub(r"\s*```$", "", json_str)

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as exc:
            last_error = f"Invalid JSON: {exc}"
            # Retry with the error message appended.
            user_message = (
                f"{user_message}\n\n"
                f"PREVIOUS ATTEMPT FAILED: The output was not valid JSON. "
                f"Error: {last_error}\n"
                f"Output ONLY the JSON object, nothing else."
            )
            continue

        # If the response includes a top-level "warnings" array, extract it.
        compiler_warnings = parsed.pop("warnings", [])

        try:
            spec = PersonaSpec(**parsed)
        except Exception as exc:
            last_error = f"Schema validation failed: {exc}"
            user_message = (
                f"{user_message}\n\n"
                f"PREVIOUS ATTEMPT FAILED: {last_error}\n"
                f"Fix the schema violations and output ONLY the JSON."
            )
            continue

        # Check rubric/out_of_scope conflicts.
        rubric = req.rubric or parsed.get("rubric")
        if rubric:
            scope_warnings = check_rubric_scope_conflict(rubric, spec.out_of_scope)
            compiler_warnings.extend(scope_warnings)

        return CompileResult(
            persona=spec,
            warnings=compiler_warnings,
            raw_response=raw_response,
        )

    return CompileResult(
        error=f"Compiler failed after {max_attempts} attempts: {last_error}",
        raw_response=raw_response,
    )


# ── Custom persona review execution ──────────────────────────────────────────

@router.post("/reviews/{review_id}/run-persona")
async def run_persona_review(review_id: str, req: RunPersonaRequest,
                             current=Depends(auth.get_current_user)):
    """Run a custom persona against a saved review.

    Uses the §4 cache-optimized prompt assembly: preamble → manuscript →
    persona → rubric → task. The manuscript is ingested at cache-miss rates
    exactly once per work; subsequent persona runs against it hit cache.
    """
    from app.ai.persona import (
        BUILTIN_PERSONAS, PersonaSpec, assemble_review_prompt,
        ARGUMENT_READER_IDS, SYNTHESIS_READER_ID, AMPLIFICATION_READER_ID,
    )

    row = db.query_one(
        "SELECT id, current_content, title FROM editorial_reviews WHERE id = %s AND user_id = %s",
        (review_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Review not found.")

    # Resolve the persona spec.
    spec = None
    persona_name = ""

    # Check built-in personas
    for p in BUILTIN_PERSONAS:
        if p["persona_id"] == req.persona_id:
            spec = PersonaSpec(**p)
            persona_name = p["name"]
            break

    # Check user's saved personas
    if spec is None:
        persona_row = db.query_one(
            "SELECT persona_json FROM editorial_personas WHERE id = %s AND user_id = %s",
            (req.persona_id, current["id"]),
        )
        if persona_row:
            spec = PersonaSpec(**json.loads(persona_row["persona_json"]))
            persona_name = spec.name

    if spec is None:
        raise HTTPException(404, f"Persona not found: {req.persona_id}")

    # Apply severity override if provided.
    if req.severity is not None:
        spec.severity = max(1, min(5, req.severity))

    # Determine if this is an Argument Reader v2 persona
    is_arg = req.persona_id in ARGUMENT_READER_IDS or req.persona_id == SYNTHESIS_READER_ID

    # Assemble the prompt in §4 order.
    manuscript = row["current_content"]
    system, user = assemble_review_prompt(manuscript, spec, rubric=req.rubric,
                                          is_argument_reader=is_arg)

    api_key, model_name, base_url = _resolve_call_model(None)

    try:
        reply = await _run_model_async(system, user, api_key, model_name, base_url,
                                       step="editorial-persona")
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise HTTPException(502, f"Model provider error: {exc}")

    # Save the run.
    run_row = db.execute(
        "INSERT INTO editorial_runs "
        "(review_id, user_id, persona_id, persona_name, rubric_json, output, severity) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at",
        (review_id, current["id"],
         req.persona_id if len(req.persona_id) > 20 else None,  # only store UUID refs
         persona_name,
         json.dumps(req.rubric) if req.rubric else None,
         reply, spec.severity),
    )

    # Also save as a report for backward compatibility with the existing reports tab.
    db.execute(
        "INSERT INTO editorial_review_reports (review_id, report_type, report, verdict) "
        "VALUES (%s, %s, %s, %s)",
        (review_id, f"persona:{persona_name}", reply, "REVIEW"),
    )
    db.execute("UPDATE editorial_reviews SET updated_at = NOW() WHERE id = %s", (review_id,))

    return {
        "run_id": str(run_row["id"]),
        "persona_name": persona_name,
        "output": reply,
        "severity": spec.severity,
        "model_used": model_name,
    }


@router.post("/reviews/{review_id}/warm-cache")
async def warm_cache(review_id: str, current=Depends(auth.get_current_user)):
    """Warm the DeepSeek cache for a review's manuscript.

    Issues a minimal call with the preamble + manuscript so the prefix is
    cached before fan-out to multiple personas. This prevents paying full
    manuscript ingestion N times when running N personas.
    """
    from app.ai.persona import assemble_cache_warmup_prompt

    row = db.query_one(
        "SELECT id, current_content FROM editorial_reviews WHERE id = %s AND user_id = %s",
        (review_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Review not found.")

    system, user = assemble_cache_warmup_prompt(row["current_content"])
    api_key, model_name, base_url = _resolve_call_model(None)

    try:
        # Use a low max_tokens to minimize cost — we just want the prefix cached.
        from app.ai.openrouter import run_chat
        await run_chat(
            api_key=api_key, model_id=model_name, base_url=base_url,
            system_prompt=system, messages=[{"role": "user", "content": user}],
            temperature=0.1, pipeline_step="cache-warmup",
        )
    except (httpx.HTTPStatusError, httpx.RequestError):
        pass  # non-fatal — the real calls will still work, just without cache benefit

    return {"warmed": True}


class RunArgumentReaderRequest(BaseModel):
    include_amplification: bool = False


@router.post("/reviews/{review_id}/run-argument-reader")
async def run_argument_reader_batch(
    review_id: str,
    req: RunArgumentReaderRequest,
    current=Depends(auth.get_current_user),
):
    """Run the full Argument Reader v2 batch: 4 readers in parallel, then synthesis.

    The 4 readers (Staffer, Academic, Columnist, Producer) are blinded from
    each other and run concurrently. After all 4 complete, the synthesis pass
    receives all 4 prose reports and findings blocks. Optionally runs the
    amplification pass last (quarantined from the manuscript).
    """
    import asyncio as _asyncio
    from app.ai.persona import (
        BUILTIN_PERSONAS, PersonaSpec, assemble_review_prompt,
        ARGUMENT_READER_IDS, SYNTHESIS_READER_ID, AMPLIFICATION_READER_ID,
        GLOBAL_PREAMBLE, _render_persona_for_prompt,
        ARGUMENT_PREAMBLE, ARGUMENT_FINDINGS_JSON_CONTRACT,
    )

    row = db.query_one(
        "SELECT id, current_content, title FROM editorial_reviews WHERE id = %s AND user_id = %s",
        (review_id, current["id"]),
    )
    if not row:
        raise HTTPException(404, "Review not found.")

    manuscript = row["current_content"]

    # Resolve the 4 reader personas.
    reader_personas = []
    for p in BUILTIN_PERSONAS:
        if p["persona_id"] in ARGUMENT_READER_IDS:
            reader_personas.append(p)

    if len(reader_personas) != 4:
        raise HTTPException(500, f"Expected 4 argument readers, found {len(reader_personas)}")

    api_key, model_name, base_url = _resolve_call_model(None)

    # Warm the cache first.
    from app.ai.persona import assemble_cache_warmup_prompt
    warmup_system, warmup_user = assemble_cache_warmup_prompt(manuscript)
    try:
        from app.ai.openrouter import run_chat
        await run_chat(
            api_key=api_key, model_id=model_name, base_url=base_url,
            system_prompt=warmup_system, messages=[{"role": "user", "content": warmup_user}],
            temperature=0.1, pipeline_step="cache-warmup",
        )
    except (httpx.HTTPStatusError, httpx.RequestError):
        pass

    # Run the 4 readers in parallel.
    async def _run_one_reader(persona_dict: dict) -> dict:
        spec = PersonaSpec(**persona_dict)
        system, user = assemble_review_prompt(manuscript, spec, is_argument_reader=True)
        try:
            reply = await _run_model_async(system, user, api_key, model_name, base_url,
                                           step=f"argument-reader:{spec.persona_id}")
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            reply = f"ERROR: {exc}"
        return {
            "persona_id": spec.persona_id,
            "name": spec.name,
            "output": reply,
        }

    # Run all 4 concurrently.
    results = await _asyncio.gather(*[_run_one_reader(p) for p in reader_personas])

    # Save each result.
    reader_outputs = []
    for r in results:
        db.execute(
            "INSERT INTO editorial_runs "
            "(review_id, user_id, persona_id, persona_name, output, severity) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (review_id, current["id"], r["persona_id"], r["name"], r["output"], 4),
        )
        db.execute(
            "INSERT INTO editorial_review_reports (review_id, report_type, report, verdict) "
            "VALUES (%s, %s, %s, %s)",
            (review_id, f"argument-reader:{r['name']}", r["output"], "REVIEW"),
        )
        reader_outputs.append(r)

    # Run synthesis pass with all 4 outputs.
    synthesis_persona = None
    for p in BUILTIN_PERSONAS:
        if p["persona_id"] == SYNTHESIS_READER_ID:
            synthesis_persona = p
            break

    synthesis_output = ""
    if synthesis_persona:
        # Build synthesis input: all 4 reports + findings blocks
        synthesis_input_parts = []
        for r in reader_outputs:
            synthesis_input_parts.append(
                f"--- {r['name'].upper()} REPORT ---\n{r['output']}\n--- END REPORT ---"
            )
        synthesis_reports = "\n\n".join(synthesis_input_parts)

        spec = PersonaSpec(**synthesis_persona)
        # Synthesis gets the reports but NOT the manuscript
        system = GLOBAL_PREAMBLE
        persona_block = _render_persona_for_prompt(spec)
        persona_block = f"{ARGUMENT_PREAMBLE}\n\n{persona_block}"
        user = (
            f"--- READER REPORTS ---\n{synthesis_reports}\n--- END REPORTS ---\n\n"
            f"--- READER PERSONA ---\n{persona_block}\n--- END PERSONA ---\n\n"
            f"TASK: Synthesize the four blinded reader reports above.{ARGUMENT_FINDINGS_JSON_CONTRACT}"
        )
        try:
            synthesis_output = await _run_model_async(system, user, api_key, model_name, base_url,
                                                      step="argument-reader:synthesis")
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            synthesis_output = f"ERROR: {exc}"

        db.execute(
            "INSERT INTO editorial_runs "
            "(review_id, user_id, persona_id, persona_name, output, severity) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (review_id, current["id"], SYNTHESIS_READER_ID, spec.name, synthesis_output, 4),
        )
        db.execute(
            "INSERT INTO editorial_review_reports (review_id, report_type, report, verdict) "
            "VALUES (%s, %s, %s, %s)",
            (review_id, f"argument-reader:{spec.name}", synthesis_output, "REVIEW"),
        )

    # Optionally run amplification pass.
    amplification_output = ""
    if req.include_amplification and synthesis_output:
        amp_persona = None
        for p in BUILTIN_PERSONAS:
            if p["persona_id"] == AMPLIFICATION_READER_ID:
                amp_persona = p
                break

        if amp_persona:
            spec = PersonaSpec(**amp_persona)
            system = GLOBAL_PREAMBLE
            persona_block = _render_persona_for_prompt(spec)
            user = (
                f"--- SYNTHESIS REPORT ---\n{synthesis_output}\n--- END SYNTHESIS ---\n\n"
                f"--- ADVISOR PERSONA ---\n{persona_block}\n--- END PERSONA ---\n\n"
                f"TASK: Advise on placement strategy based on the synthesis above."
            )
            try:
                amplification_output = await _run_model_async(system, user, api_key, model_name, base_url,
                                                              step="argument-reader:amplification")
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                amplification_output = f"ERROR: {exc}"

            db.execute(
                "INSERT INTO editorial_runs "
                "(review_id, user_id, persona_id, persona_name, output, severity) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (review_id, current["id"], AMPLIFICATION_READER_ID, spec.name, amplification_output, 3),
            )

    db.execute("UPDATE editorial_reviews SET updated_at = NOW() WHERE id = %s", (review_id,))

    return {
        "readers": [
            {"persona_id": r["persona_id"], "name": r["name"], "output": r["output"]}
            for r in reader_outputs
        ],
        "synthesis": {"output": synthesis_output, "name": "Synthesis"} if synthesis_output else None,
        "amplification": {"output": amplification_output, "name": "Amplification Strategy"} if amplification_output else None,
        "model_used": model_name,
    }


@router.get("/reviews/{review_id}/runs")
async def list_runs(review_id: str, current=Depends(auth.get_current_user)):
    """List all persona runs for a review (for comparison view)."""
    rows = db.query_all(
        "SELECT id, persona_name, severity, output, cache_hit_tokens, "
        "cache_miss_tokens, cost_usd, created_at "
        "FROM editorial_runs WHERE review_id = %s AND user_id = %s ORDER BY created_at DESC",
        (review_id, current["id"]),
    )
    return {
        "runs": [
            {
                "id": str(r["id"]),
                "persona_name": r["persona_name"],
                "severity": r["severity"],
                "output": r["output"],
                "cache_hit_tokens": r["cache_hit_tokens"],
                "cache_miss_tokens": r["cache_miss_tokens"],
                "cost_usd": float(r["cost_usd"]) if r["cost_usd"] else 0,
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ]
    }
