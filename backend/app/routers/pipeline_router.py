"""Web pipeline routes — authenticated, user-scoped, with version capture.

Wraps the ported ``orchestrator`` / ``critics`` / ``outputs`` modules. Each
route resolves ``project_id`` to an on-disk path under the current user, so the
existing pipeline tools (which only need a path) work unchanged. The
``advance-phase`` route additionally captures a version snapshot of every
artifact a phase produces.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import pathlib
import re
import time
import zipfile
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import auth, config, db, settings_store, versions
from app.pipeline import orchestrator, outputs, critics

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])
log = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _resolve_project(project_id: str, user_id: str) -> str:
    """Verify ownership in the DB and return the on-disk project path."""
    row = db.query_one(
        "SELECT id, name FROM projects WHERE id = %s AND user_id = %s",
        (project_id, user_id),
    )
    if not row:
        raise HTTPException(404, "Project not found.")
    pdir = config.project_path(user_id, project_id)
    pdir.mkdir(parents=True, exist_ok=True)
    return os.path.realpath(str(pdir))


def _project_name(project_id: str, user_id: str) -> str:
    row = db.query_one(
        "SELECT name FROM projects WHERE id = %s AND user_id = %s",
        (project_id, user_id),
    )
    return row["name"] if row else "Untitled"


def _is_html_body(body: str) -> bool:
    """Return True if the response body looks like an HTML page (not API JSON).

    Robust against a leading UTF-8 BOM (``\\ufeff``) and CR/LF/whitespace that
    some providers (or WAFs like Cloudflare) emit before ``<!DOCTYPE html>``.
    """
    if not body:
        return False
    # Strip BOM and all leading whitespace before checking the first char.
    stripped = body.lstrip("\ufeff \t\r\n")
    if stripped.startswith("<"):
        return True
    # Some providers embed HTML after a short preamble — scan the first 500 chars.
    return bool(re.search(r'<!DOCTYPE\s+html|<html[\s>]', body[:500], re.IGNORECASE))


def _sanitize_error(msg: str | None) -> str | None:
    """Strip HTML page content from error messages before showing them to the user.

    When a provider returns a Cloudflare or server HTML page, the exception
    string contains the full DOCTYPE/HTML. We keep the first meaningful
    non-HTML lines (e.g. 'HTTPStatusError: Client error 400 for url ...')
    and drop everything from the first HTML tag onward.
    """
    if not msg:
        return msg
    # Strip a BOM from the whole message first so a BOM on the first line
    # doesn't prevent the HTML detection below from triggering.
    msg = msg.lstrip("\ufeff")
    lines = msg.splitlines()
    clean_lines: list[str] = []
    for line in lines:
        stripped_line = line.lstrip("\ufeff \t")
        if stripped_line.startswith("<") or stripped_line.startswith("<!"):
            break          # drop this line and everything after
        clean_lines.append(line)
    result = "\n".join(clean_lines).strip()
    return result or "The provider returned an error. Check your API key and model in Settings."


def _provider_exc(e: httpx.HTTPStatusError) -> HTTPException:
    status = e.response.status_code
    if status == 401:
        return HTTPException(401, "API key is invalid. Check the provider key in Settings.")
    if status == 402:
        return HTTPException(402, "Insufficient credits on the provider account.")
    if status == 429:
        return HTTPException(429, "Rate limited by the provider. Wait a moment and retry.")
    if status >= 500:
        return HTTPException(502, "The model provider returned a server error. Try again in a moment.")
    # Extract a plain-text detail — discard HTML error pages (Cloudflare, etc.)
    detail = ""
    try:
        body = e.response.text or ""
        if _is_html_body(body):
            # HTML page (Cloudflare WAF block, maintenance page, etc.)
            # Don't show raw HTML — give a human message instead.
            detail = (
                f"The provider blocked the request (HTTP {status}). "
                "This may be caused by an unsupported header or WAF rule."
            )
        else:
            # Try to pull "message" or "error" from JSON
            try:
                import json as _json
                j = _json.loads(body)
                detail = (j.get("error", {}) or {}).get("message", "") or j.get("message", "") or body[:200]
            except Exception:
                detail = body[:200]
    except Exception:
        pass
    return HTTPException(502, f"Provider error (HTTP {status}): {detail}" if detail else f"Provider returned HTTP {status}.")


def _resolve_call_model(qualified: str | None):
    from app.ai.providers import resolve
    target = (qualified or settings_store.get_default_model() or "").strip()
    if not target:
        raise HTTPException(
            status_code=400,
            detail=(
                "No model is configured. Go to Settings → Model routing and "
                "choose a Default model, then save."
            ),
        )
    try:
        resolved = resolve(target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not resolved.is_configured:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The '{resolved.label}' provider isn't configured. "
                f"Add its API key in Settings → Providers."
            ),
        )
    return resolved.api_key, resolved.model_name, resolved.base_url


def _make_model_call(
    api_key: str,
    model_name: str,
    base_url: str,
    provider_id: str = "unknown",
    phase: str = "unknown",
    chapter: int | None = None,
    project_path: str = "",
    call_log: "CallLogWriter | None" = None,
    _transport: "httpx.AsyncBaseTransport | None" = None,
    _is_switch: bool = False,
    _switched_from: str = "",
):
    """Build an injectable async model call with classified retry logic.

    Ported from Standalone (pipeline.py:405-604) with these deliberate
    divergences:
      - Tracking headers use https://openwrite.app (not localhost:1420).
      - Tracking headers conditional on OpenRouter base URL.
      - _transport parameter for test injection (production uses None).

    Three independent budgets (R1):
      - transport_attempts: per-call, for classes 1-3 (network/rate-limit/server)
      - content_attempts: per-call, for classes 5/7/8 (refusal/empty/malformed)
      - quality_attempts: per-chapter, managed by the orchestrator (not here)

    Provider switching is an explicit decision (R2): permitted for classes
    2, 3, 5, 7 only. The switch callback is injected, not inferred from
    exception handlers.

    Every call is recorded (R5).
    """
    from app.ai.failure_classifier import (
        FailureClass, Action, classify_response, classify_exception,
    )
    from app.ai.call_recorder import make_record, CallLogWriter as _CLW

    OPENROUTER_BASE = "https://openrouter.ai/api/v1"
    PIPELINE_CALL_TIMEOUT = 120.0

    # Switch callback: set by the resolver to enable provider switching.
    # Signature: async (system_prompt, user_prompt) -> str
    # None = switching not available for this call.
    _switch_call: "ModelCall | None" = None
    _switch_provider: str = ""

    def set_switch(switch_call: "ModelCall | None", switch_provider: str = ""):
        nonlocal _switch_call, _switch_provider
        _switch_call = switch_call
        _switch_provider = switch_provider

    model_call = _ModelCallWithSwitch(set_switch=set_switch)

    async def _execute(system_prompt: str, user_prompt: str) -> str:
        transport_budget = 5   # classes 1-3
        content_budget = 2     # classes 5/7/8
        switched = False

        for transport_attempt in range(1, transport_budget + 1):
            start = time.monotonic()
            finish_reason = None
            content_val = None
            http_status = 200
            error_body = None
            retry_after = None

            try:
                async with httpx.AsyncClient(
                    timeout=PIPELINE_CALL_TIMEOUT, transport=_transport,
                ) as client:
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    }
                    if OPENROUTER_BASE in base_url.rstrip("/"):
                        headers["HTTP-Referer"] = "https://openwrite.app"
                        headers["X-Title"] = "Open-Write"

                    response = await client.post(
                        f"{base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json={
                            "model": model_name,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            "temperature": 0.4,
                        },
                    )
                    elapsed = round((time.monotonic() - start) * 1000)
                    http_status = response.status_code
                    retry_after = response.headers.get("retry-after")

                    if response.status_code >= 400:
                        try:
                            error_body = response.json()
                        except Exception:
                            error_body = {"raw": response.text[:500]}
                        cls = classify_response(
                            http_status=http_status,
                            finish_reason=None,
                            content=None,
                            error_body=error_body,
                            retry_after_header=retry_after,
                        )
                    else:
                        data = response.json()
                        choice = data.get("choices", [{}])[0]
                        msg = choice.get("message", {})
                        finish_reason = choice.get("finish_reason")
                        content_val = msg.get("content")
                        cls = classify_response(
                            http_status=http_status,
                            finish_reason=finish_reason,
                            content=content_val,
                        )

            except Exception as exc:
                elapsed = round((time.monotonic() - start) * 1000)
                cls = classify_exception(exc)

            # ── Record the call (R5) ─────────────────────────────────
            if call_log is not None:
                rec = make_record(
                    phase=phase,
                    chapter=chapter,
                    model=model_name,
                    provider=provider_id,
                    failure_class=cls.failure_class.name.lower() if cls.failure_class != FailureClass.OK else "ok",
                    attempt=transport_attempt,
                    switched=_is_switch or switched,
                    switched_to=_switched_from if _is_switch else (_switch_provider if switched else None),
                    finish_reason=finish_reason,
                    elapsed_ms=elapsed,
                    detail=cls.detail,
                    transport_attempts=transport_attempt,
                    content_attempts=content_budget,
                )
                call_log.write(rec)

            # ── OK — return content ──────────────────────────────────
            if cls.failure_class == FailureClass.OK:
                from app.ai.sanitizer import sanitize
                return sanitize(content_val) if content_val is not None else ""

            # ── Class 4: Auth/payment — halt immediately ─────────────
            if cls.failure_class == FailureClass.AUTH_PAYMENT:
                raise httpx.HTTPStatusError(
                    message=cls.detail,
                    request=httpx.Request("POST", f"{base_url}/chat/completions"),
                    response=httpx.Response(http_status, json=error_body),
                )

            # ── Classes where retry_same is not allowed ──────────────
            if not cls.retry_same_allowed:
                # Class 5 (Refusal): switch provider if available (R2)
                if cls.failure_class == FailureClass.REFUSAL and _switch_call and not switched:
                    switched = True
                    try:
                        return await _switch_call(system_prompt, user_prompt)
                    except Exception:
                        pass  # Switch failed — fall through to halt
                # Class 6 (Truncation): halt unit, preserve partial content
                if cls.failure_class == FailureClass.TRUNCATION:
                    raise _TruncationError(
                        cls.detail,
                        partial_content=content_val or "",
                        finish_reason=finish_reason,
                    )
                # All others: halt
                raise _ModelCallFailure(cls.detail, cls.failure_class)

            # ── Transport retry with backoff (classes 1-3) ───────────
            if cls.action == Action.RETRY_WITH_BACKOFF:
                if transport_attempt < transport_budget:
                    if cls.retry_after:
                        delay = cls.retry_after
                    elif transport_attempt - 1 < len(cls.backoff_schedule):
                        delay = cls.backoff_schedule[transport_attempt - 1]
                    else:
                        delay = cls.backoff_schedule[-1] if cls.backoff_schedule else 30

                    # Provider switch after 3 attempts (classes 2, 3)
                    if transport_attempt >= 3 and cls.switch_allowed and _switch_call and not switched:
                        switched = True
                        try:
                            return await _switch_call(system_prompt, user_prompt)
                        except Exception:
                            pass  # Switch failed — continue retrying same

                    await asyncio.sleep(delay)
                    continue

            # ── Content retry (classes 5/7/8) ────────────────────────
            if content_budget > 0 and cls.retry_same_allowed:
                content_budget -= 1
                continue

            # ── Exhausted — raise ────────────────────────────────────
            raise _ModelCallFailure(cls.detail, cls.failure_class)

        # All transport attempts exhausted
        raise _ModelCallFailure("transport retry budget exhausted", FailureClass.NETWORK_TRANSIENT)

    model_call._execute = _execute
    return model_call


class _ModelCallFailure(Exception):
    """Raised when a model call fails after all retries."""
    def __init__(self, detail: str, failure_class: "FailureClass"):
        super().__init__(detail)
        self.failure_class = failure_class


class _TruncationError(Exception):
    """Raised when output is truncated. Preserves partial content."""
    def __init__(self, detail: str, partial_content: str, finish_reason: str | None):
        super().__init__(detail)
        self.partial_content = partial_content
        self.finish_reason = finish_reason


class _ModelCallWithSwitch:
    """Wrapper that presents as a ModelCall but carries a switch setter.

    The orchestrator receives this as a ModelCall (async callable). The
    resolver uses set_switch() to inject the secondary provider before
    handing it to the orchestrator.
    """
    def __init__(self, set_switch):
        self._execute = None
        self._set_switch = set_switch

    async def __call__(self, system_prompt: str, user_prompt: str) -> str:
        return await self._execute(system_prompt, user_prompt)

    def set_switch(self, switch_call, switch_provider: str = ""):
        self._set_switch(switch_call, switch_provider)


def _build_phase_resolver(project_path: str = ""):
    """Return a `resolve_for_phase(phase)` callable used by advance-phase and
    the background tasks. Requires user settings to already be bound (via the
    request context or `settings_store.bind_user_settings`).

    ``project_path`` is passed to the call recorder for per-project log files.
    """
    from app.ai.call_recorder import CallLogWriter
    _cache: dict[str, tuple[str, str, str]] = {}
    _call_log = CallLogWriter(project_path) if project_path else None

    def _get_info(mid: str) -> tuple[str, str, str]:
        if mid not in _cache:
            _cache[mid] = _resolve_call_model(mid)
        return _cache[mid]

    def _resolve(phase: str):
        model_id = settings_store.get_model_for_phase(phase)
        key, name, base = _get_info(model_id)
        provider_id = model_id.split("/")[0] if "/" in model_id else "unknown"
        call = _make_model_call(
            key, name, base,
            provider_id=provider_id,
            phase=phase,
            project_path=project_path,
            call_log=_call_log,
        )
        # R2: Provider switching is an explicit decision, not an exception handler.
        # R3: A critic must never fall back to the writer model for the same unit.
        if phase in ("critics", "editorial"):
            author_id = settings_store.get_writer_model()
            if model_id != author_id:
                a_key, a_name, a_base = _get_info(author_id)
                switch_call = _make_model_call(
                    a_key, a_name, a_base,
                    provider_id=author_id.split("/")[0] if "/" in author_id else "unknown",
                    phase=phase,
                    project_path=project_path,
                    call_log=_call_log,
                    _is_switch=True,
                    _switched_from=model_id,
                )
                call.set_switch(switch_call, switch_provider=author_id)
            # else: same model — no switch provided. R3: the critic fails
            # with _ModelCallFailure rather than self-critiquing.
        return call
    return _resolve


# ── Request models ────────────────────────────────────────────────────────────
class StartRunRequest(BaseModel):
    project_name: str = ""
    word_floor: int = 800
    instructions: str = ""
    rerun_mode: str = "fresh"
    max_chapter_retries: int = 2


class AdvancePhaseRequest(BaseModel):
    model_id: str | None = None
    instructions: str = ""


class PipelineMessage(BaseModel):
    role: str
    content: str


class PipelineChatRequest(BaseModel):
    messages: list[PipelineMessage]
    model_id: str | None = None
    context_artifact: str | None = None
    context_chapter: int | None = None


class UpdateInstructionsRequest(BaseModel):
    instructions: str


class SetStatusRequest(BaseModel):
    status: str


class ResetRunRequest(BaseModel):
    confirm: bool = True
    phase: str | None = None            # if set: rewind to this phase (preserve all prior work)
    chapter: int | None = None          # if set: rewind to this chapter (only for unit phases)
    max_chapter_retries: int | None = None  # override the revision-round limit


class SetOverrideRequest(BaseModel):
    phase_key: str
    content: str


class RerunPhaseRequest(BaseModel):
    phase: str
    chapter: int | None = None


class StartRevisionRequest(BaseModel):
    chapters: list[int]
    revision_notes: str = ""


# ── Routes ──────────────────────────────────────────────────────────────────
@router.post("/{project_id}/start-run")
async def start_run(project_id: str, req: StartRunRequest,
                    current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    name = req.project_name or _project_name(project_id, current["id"])
    state = orchestrator.start_run(project, name, req.word_floor,
                                   instructions=req.instructions,
                                   rerun_mode=req.rerun_mode,
                                   max_chapter_retries=req.max_chapter_retries)
    return {
        "status": state.status,
        "current_phase": state.current_phase,
        "current_phase_label": orchestrator.PHASE_SPECS[state.current_phase].label,
        "units": state.units,
        "instructions": state.instructions,
        "run_state": state.to_dict(),
    }


@router.get("/{project_id}/run-state")
async def run_state(project_id: str, current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    state = orchestrator.load_run_state(project)
    if state is None:
        return {"active": False}

    # Sanitize any stored HTML error message (e.g. from a previous Cloudflare
    # block). Clean it in place and re-save so the JSON file is also fixed.
    cleaned_error = _sanitize_error(state.last_error)
    if cleaned_error != state.last_error:
        state.last_error = cleaned_error
        orchestrator.save_run_state(state)

    current_spec = orchestrator.PHASE_SPECS.get(state.current_phase)
    return {
        "active": True,
        "status": state.status,
        "current_phase": state.current_phase,
        "current_phase_label": current_spec.label if current_spec else state.current_phase,
        "current_unit_index": state.current_unit_index,
        "units": state.units,
        "instructions": state.instructions,
        "last_error": state.last_error,
        "phase_results": state.phase_results,
        "unit_results": {str(k): v for k, v in state.unit_results.items()},
        "revision_chapters": state.revision_chapters,
        "revision_notes": state.revision_notes,
        "max_chapter_retries": state.max_chapter_retries,
        "phase_order": [
            {"key": p.key, "label": p.label, "scope": p.scope}
            for p in orchestrator.PHASE_SPECS.values()
        ],
    }


@router.post("/{project_id}/start-revision")
async def start_revision(project_id: str, req: StartRevisionRequest,
                         current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    try:
        state = await orchestrator.start_revision(project, req.chapters, req.revision_notes)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "status": state.status,
        "current_phase": state.current_phase,
        "current_unit_index": state.current_unit_index,
        "revision_chapters": state.revision_chapters,
    }


@router.get("/{project_id}/editorial-reports")
async def editorial_reports(project_id: str, current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    state = orchestrator.load_run_state(project)
    if state is None:
        return {"reports": []}
    reports = []
    for ch in (state.units or []):
        path = pathlib.Path(project) / "coverage_reports" / f"editorial_report_ch{ch}.md"
        content = path.read_text(encoding="utf-8-sig") if path.exists() else None
        reports.append({"chapter": ch, "content": content})
    return {"reports": reports}


@router.get("/{project_id}/phase-order")
async def phase_order(project_id: str, current=Depends(auth.get_current_user)):
    _resolve_project(project_id, current["id"])
    return {
        "phases": [
            {"key": p.key, "label": p.label, "scope": p.scope, "gate": p.gate_phase}
            for p in orchestrator.PHASE_SPECS.values()
        ]
    }


@router.post("/{project_id}/advance-phase")
async def advance_phase(project_id: str, req: AdvancePhaseRequest,
                        current=Depends(auth.get_current_user)):
    """Start exactly one phase as a background task and return immediately.

    Long phases (e.g. a full chapter from a reasoning model) can exceed the
    hosting proxy's ~100s HTTP timeout. We run the phase in a background
    asyncio task and return right away; the frontend polls ``run-state`` for
    progress and ``phase-task-result`` for the artifact preview.
    """
    project = _resolve_project(project_id, current["id"])

    # Reject if a single-phase task is already running for this project.
    existing = _phase_tasks.get(project_id)
    if existing is not None and not existing.done():
        raise HTTPException(status_code=409, detail="A phase is already running for this project.")

    # Reject if auto-run is actively running — it drives phases itself and
    # holds the lock. A stopped-but-lingering task does NOT block here.
    ar_key = _ar_key(current["id"], project_id)
    if _ar_status.get(ar_key, {}).get("running", False):
        raise HTTPException(status_code=409, detail="Auto-run is active. Stop it before running a single phase.")

    state = orchestrator.load_run_state(project)
    if state is None or not state.current_phase:
        raise HTTPException(status_code=409, detail="No active phase to run. Start a run first.")
    spec = orchestrator.PHASE_SPECS.get(state.current_phase)
    label = spec.label if spec else state.current_phase

    task = asyncio.create_task(
        _run_phase_task(project_id, current["id"], req.instructions, project)
    )
    _phase_tasks[project_id] = task
    return {
        "phase_started": True,
        "current_phase": state.current_phase,
        "current_phase_label": label,
    }


@router.get("/{project_id}/phase-task-result")
async def phase_task_result(project_id: str, current=Depends(auth.get_current_user)):
    """Return the most recent completed phase result (or error) for preview."""
    _resolve_project(project_id, current["id"])
    return _phase_results.get(project_id, {})


@router.get("/{project_id}/outputs")
async def get_outputs(project_id: str, current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    return outputs.build_output_catalog(project, word_counts=True)


@router.get("/{project_id}/output-file")
async def get_output_file(project_id: str, path: str,
                          current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    try:
        return outputs.read_artifact(project, path)
    except ValueError:
        raise HTTPException(404, "Artifact not found or out of bounds.")


@router.get("/{project_id}/export")
async def export_project(project_id: str, current=Depends(auth.get_current_user)):
    """Download the project's generated files as a ZIP archive."""
    project = _resolve_project(project_id, current["id"])
    project_name = _project_name(project_id, current["id"])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        project_path = pathlib.Path(project)
        for file_path in sorted(project_path.rglob("*")):
            if file_path.is_file():
                # Use a clean relative path inside the zip
                rel = file_path.relative_to(project_path)
                zf.write(file_path, str(rel))
    buf.seek(0)

    safe_name = re.sub(r'[^\w\s-]', '', project_name).strip().replace(' ', '_') or "project"
    filename = f"{safe_name}_export.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{project_id}/export-from-versions")
async def export_from_versions(project_id: str, current=Depends(auth.get_current_user)):
    """Recover project content from the version history in the database.

    Builds a ZIP from the versions table instead of the filesystem, so content
    is recoverable even if on-disk files are lost (e.g. ephemeral container storage).
    """
    _resolve_project(project_id, current["id"])
    project_name = _project_name(project_id, current["id"])

    rows = db.query_all(
        "SELECT content_type, chapter_number, phase, content, created_at "
        "FROM versions WHERE project_id = %s ORDER BY created_at ASC",
        (project_id,),
    )

    if not rows:
        raise HTTPException(404, "No version history found for this project.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen: dict[str, int] = {}
        for row in rows:
            ct = row["content_type"]
            ch = row["chapter_number"]
            content = row["content"] or ""
            # Build a filename: content_type + optional chapter suffix
            if ch is not None:
                base = f"{ct}_ch{ch}"
            else:
                base = ct
            # Deduplicate: if same base appears multiple times, suffix with a counter
            count = seen.get(base, 0)
            seen[base] = count + 1
            if count > 0:
                fname = f"versions/{base}_v{count + 1}.md"
            else:
                fname = f"versions/{base}.md"
            zf.writestr(fname, content)

        # Add a manifest listing what's in the archive
        manifest_lines = [f"# Version History Export — {project_name}", ""]
        for row in rows:
            ts = row["created_at"].isoformat() if row.get("created_at") else "?"
            ch_str = f" ch{row['chapter_number']}" if row["chapter_number"] else ""
            manifest_lines.append(
                f"- {row['content_type']}{ch_str} ({row['phase']}) — {ts}"
            )
        zf.writestr("versions/MANIFEST.md", "\n".join(manifest_lines))

    buf.seek(0)
    safe_name = re.sub(r'[^\w\s-]', '', project_name).strip().replace(' ', '_') or "project"
    filename = f"{safe_name}_version_history.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{project_id}/reset-run")
async def reset_run(project_id: str, req: ResetRunRequest,
                    current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    if req.phase:
        # Rewind mode: preserve all prior work, just move the cursor.
        try:
            state = await orchestrator.prepare_rerun(project, req.phase, req.chapter)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except orchestrator.PhaseBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        if state is None:
            raise HTTPException(404, "No active run to rewind. Start a run first.")
        # Optionally update the revision-round limit.
        if req.max_chapter_retries is not None:
            state.max_chapter_retries = max(1, req.max_chapter_retries)
            orchestrator.save_run_state(state)
        return {
            "reset": True,
            "mode": "rewind",
            "current_phase": state.current_phase,
            "current_phase_label": orchestrator.PHASE_SPECS[state.current_phase].label,
            "current_unit_index": state.current_unit_index,
            "max_chapter_retries": state.max_chapter_retries,
        }
    else:
        # Full reset (existing behavior): delete pipeline_run.json.
        # Cancel any active auto-run first so it releases the orchestrator lock.
        await _cancel_ar_task(_ar_key(current["id"], project_id))
        orchestrator.reset_run(project)
        return {"reset": True, "mode": "full"}


# ── Server-side auto-run ─────────────────────────────────────────────────────
# In-memory state: cleared on backend restart (pipeline_run.json survives on disk).
_ar_tasks:  dict[str, asyncio.Task] = {}   # key -> running asyncio Task
_ar_logs:   dict[str, list[dict]]  = {}    # key -> [{time, message, type}]
_ar_status: dict[str, dict]        = {}    # key -> {running, countdown, failed, failed_phase}

# Single-phase "advance-phase" background tasks (separate from auto-run).
_phase_tasks:   dict[str, asyncio.Task] = {}   # project_id -> running phase task
_phase_results: dict[str, dict]         = {}   # project_id -> last result (or {"error": ...})


def _ar_key(user_id: str, project_id: str) -> str:
    return f"{user_id}/{project_id}"


async def _cancel_ar_task(key: str) -> None:
    """Cancel a lingering auto-run task and wait for it to wind down.

    Sets the running flag to False, cancels the asyncio Task, and awaits it so
    the orchestrator's per-project lock is released before the caller proceeds.
    Safe to call when no task exists (no-op).
    """
    _ar_status.setdefault(key, {"running": False, "countdown": 0, "failed": False, "failed_phase": ""})
    _ar_status[key]["running"] = False
    task = _ar_tasks.get(key)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _ar_tasks.pop(key, None)


def _clean_for_log(msg: str) -> str:
    """Strip HTML and truncate long error messages for display in the auto-run log."""
    if not msg:
        return msg
    if re.search(r'<!DOCTYPE\s+html|<html[\s>]', msg[:500], re.IGNORECASE):
        return ("The provider returned an HTML error page (likely a WAF/Cloudflare block). "
                "Check your API key, model name, and provider status.")
    return msg[:300] + "…" if len(msg) > 300 else msg


async def _run_phase_task(project_id: str, user_id: str, instructions: str, project: str) -> None:
    """Background task that runs a single phase and stores the result/error.

    On completion the result (with captured versions) is stored in
    ``_phase_results[project_id]``; on failure ``{"error": <clean message>}``
    is stored. The orchestrator itself persists success/failure to
    ``pipeline_run.json`` so the frontend also sees progress via run-state.
    """
    try:
        settings_store.bind_user_settings(user_id)
        resolve_for_phase = _build_phase_resolver(project_path=project)
        try:
            result = await orchestrator.advance_phase(project, resolve_for_phase)
        except (orchestrator.PhaseBusyError, RuntimeError) as exc:
            _phase_results[project_id] = {"error": str(exc)}
            return
        except httpx.HTTPStatusError as exc:
            # Re-save the run-state error as a clean message (may contain WAF HTML).
            try:
                clean_state = orchestrator.load_run_state(project)
                if clean_state and clean_state.last_error:
                    clean_state.last_error = _sanitize_error(clean_state.last_error)
                    orchestrator.save_run_state(clean_state)
            except Exception:
                pass
            _phase_results[project_id] = {"error": _clean_for_log(str(exc))}
            return
        except Exception as exc:  # catch-all (e.g. JSONDecodeError from HTML w/ 200)
            _phase_results[project_id] = {"error": _clean_for_log(_sanitize_error(str(exc)) or str(exc))}
            return

        try:
            captured = versions.capture_phase_versions(project_id, user_id, project, result)
            result["captured_versions"] = captured
        except Exception as exc:  # never let capture break the pipeline
            result["captured_versions_error"] = str(exc)

        _phase_results[project_id] = result
    except Exception as exc:  # last-resort safety net
        _phase_results[project_id] = {"error": _clean_for_log(str(exc))}
    finally:
        _phase_tasks.pop(project_id, None)


async def _auto_run_loop(key: str, project_id: str, user_id: str, instructions: str) -> None:
    """Asyncio background task that runs the pipeline phases with retry logic."""
    def _now() -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _log(message: str, log_type: str) -> None:
        _ar_logs[key].append({"time": _now(), "message": message, "type": log_type})

    try:
        # Bind user settings so settings_store contextvar works in this task.
        settings_store.bind_user_settings(user_id)

        project = os.path.realpath(str(config.project_path(user_id, project_id)))

        resolve_for_phase = _build_phase_resolver(project_path=project)

        while _ar_status[key]["running"]:
            state = orchestrator.load_run_state(project)
            if state is None:
                _log("No active run found.", "error")
                break
            if state.status == "complete" or not state.current_phase:
                _log("Pipeline complete 🎉", "info")
                break

            spec = orchestrator.PHASE_SPECS.get(state.current_phase)
            label = spec.label if spec else state.current_phase
            _log(f"Starting phase: {label}", "info")

            success = False
            for attempt in range(1, 5):
                if not _ar_status[key]["running"]:
                    break

                # Heartbeat: log "still working" every 90s so the user sees the
                # phase is alive even while a slow model is thinking.
                phase_start = asyncio.get_event_loop().time()
                heartbeat_running = True

                async def _heartbeat() -> None:
                    while heartbeat_running:
                        await asyncio.sleep(90)
                        if heartbeat_running:
                            elapsed = int(asyncio.get_event_loop().time() - phase_start)
                            _log(f"⏳ Still working… ({elapsed}s elapsed)", "info")

                hb_task = asyncio.create_task(_heartbeat())
                try:
                    try:
                        result = await orchestrator.advance_phase(project, resolve_for_phase)
                    finally:
                        # Stop the heartbeat as soon as the phase returns/raises.
                        heartbeat_running = False
                        hb_task.cancel()
                        try:
                            await hb_task
                        except asyncio.CancelledError:
                            pass
                    try:
                        versions.capture_phase_versions(project_id, user_id, project, result)
                    except Exception:
                        pass
                    if attempt > 1:
                        _log(f"Phase {label} completed on attempt {attempt}/4 ✓", "info")
                    else:
                        _log(f"Phase {label} completed ✓", "info")
                    success = True
                    break
                except orchestrator.BadProseError as exc:
                    err_msg = _clean_for_log(str(exc))
                    _log(f"Chapter prose generation failed permanently: {err_msg}", "error")
                    _ar_status[key]["failed"] = True
                    _ar_status[key]["failed_phase"] = label
                    success = False
                    break  # don't retry at phase level - already exhausted all prose retries
                except Exception as exc:
                    err_msg = _clean_for_log(str(exc))
                    _log(f"Phase {label} failed (attempt {attempt}/4): {err_msg}", "error")
                    if attempt == 4:
                        break
                    if attempt == 3:
                        _log("Waiting 5 minutes before final retry…", "warn")
                        for remaining in range(300, 0, -1):
                            if not _ar_status[key]["running"]:
                                break
                            _ar_status[key]["countdown"] = remaining
                            await asyncio.sleep(1)
                        _ar_status[key]["countdown"] = 0
                        if not _ar_status[key]["running"]:
                            break
                        _log("5-minute wait complete — retrying now…", "warn")
                    else:
                        for _ in range(30):
                            if not _ar_status[key]["running"]:
                                break
                            await asyncio.sleep(0.1)

            if not _ar_status[key]["running"]:
                _log("Auto-run stopped by user", "info")
                break

            if not success:
                _ar_status[key]["failed"] = True
                _ar_status[key]["failed_phase"] = label
                _log(f"Auto-run stopped: too many failures on phase {label}", "error")
                break

            for _ in range(10):
                if not _ar_status[key]["running"]:
                    break
                await asyncio.sleep(0.1)

    except Exception as exc:
        _log(f"Auto-run crashed: {_clean_for_log(str(exc))}", "error")
    finally:
        _ar_status[key]["running"] = False
        _ar_status[key]["countdown"] = 0
        _ar_tasks.pop(key, None)


class AutoRunRequest(BaseModel):
    instructions: str = ""


@router.post("/{project_id}/auto-run/start")
async def auto_run_start(project_id: str, req: AutoRunRequest,
                         current=Depends(auth.get_current_user)):
    _resolve_project(project_id, current["id"])
    key = _ar_key(current["id"], project_id)

    # If a previous task is genuinely still running, reject.
    old = _ar_tasks.get(key)
    if old is not None and not old.done():
        if _ar_status.get(key, {}).get("running", False):
            return {"started": False, "reason": "already_running"}
        # Stopped but still winding down — cancel it so we can start fresh.
        await _cancel_ar_task(key)

    _ar_logs[key] = []
    _ar_status[key] = {"running": True, "countdown": 0, "failed": False, "failed_phase": ""}
    task = asyncio.create_task(_auto_run_loop(key, project_id, current["id"], req.instructions))
    _ar_tasks[key] = task
    return {"started": True}


@router.post("/{project_id}/auto-run/stop")
async def auto_run_stop(project_id: str, current=Depends(auth.get_current_user)):
    _resolve_project(project_id, current["id"])
    key = _ar_key(current["id"], project_id)
    await _cancel_ar_task(key)
    return {"stopped": True}


@router.get("/{project_id}/auto-run/status")
async def auto_run_status(project_id: str, current=Depends(auth.get_current_user)):
    _resolve_project(project_id, current["id"])
    key = _ar_key(current["id"], project_id)
    status = _ar_status.get(key, {"running": False, "countdown": 0, "failed": False, "failed_phase": ""})
    log = _ar_logs.get(key, [])
    return {
        "running": status.get("running", False),
        "countdown": status.get("countdown", 0),
        "failed": status.get("failed", False),
        "failed_phase": status.get("failed_phase", ""),
        "log": log,
    }


@router.post("/{project_id}/update-instructions")
async def update_instructions(project_id: str, req: UpdateInstructionsRequest,
                              current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    try:
        state = await orchestrator.update_instructions(project, req.instructions)
    except orchestrator.PhaseBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if state is None:
        raise HTTPException(404, "No active run. Start a run first.")
    return {"status": state.status, "instructions": state.instructions}


@router.post("/{project_id}/set-status")
async def set_status(project_id: str, req: SetStatusRequest,
                     current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    if req.status not in ("running", "paused", "complete", "failed"):
        raise HTTPException(400, "status must be running|paused|complete|failed")
    try:
        state = await orchestrator.set_status(project, req.status)
    except orchestrator.PhaseBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if state is None:
        raise HTTPException(404, "No active run. Start a run first.")
    return {"status": state.status}


@router.post("/{project_id}/rerun-phase")
async def rerun_phase(project_id: str, req: RerunPhaseRequest,
                      current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    try:
        state = await orchestrator.prepare_rerun(project, req.phase, req.chapter)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except orchestrator.PhaseBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if state is None:
        raise HTTPException(404, "No active run. Start a run first.")
    return {
        "status": state.status,
        "current_phase": state.current_phase,
        "current_phase_label": orchestrator.PHASE_SPECS[state.current_phase].label,
        "current_unit_index": state.current_unit_index,
        "units": state.units,
    }


@router.post("/{project_id}/set-override")
async def set_override(project_id: str, req: SetOverrideRequest,
                       current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    state = orchestrator.load_run_state(project)
    if state is None:
        raise HTTPException(404, "No active run.")
    state.user_overrides[req.phase_key] = req.content
    orchestrator.save_run_state(state)
    return {"ok": True, "key": req.phase_key}


# ── Pipeline chat companion ────────────────────────────────────────────────
_BRIEF_BLOCK_RE = re.compile(
    r"\bSUGGESTED_BRIEF\s*:\s*\n(.*?)\n\s*:END\b", re.IGNORECASE | re.DOTALL,
)


def _extract_suggested_brief(text: str) -> tuple[str, str | None]:
    matches = _BRIEF_BLOCK_RE.findall(text)
    if not matches:
        return text, None
    suggested = matches[-1].strip()
    m_last = None
    for m in _BRIEF_BLOCK_RE.finditer(text):
        m_last = m
    clean = (text[:m_last.start()] + text[m_last.end():]).strip() if m_last else text
    return clean, suggested


def _strip_control_tokens(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _BRIEF_BLOCK_RE.sub("[control token removed]", normalized)
    cleaned = re.sub(r"\bSUGGESTED_BRIEF\b", "[token removed]", cleaned, flags=re.IGNORECASE)
    return cleaned


def _pipeline_chat_system_prompt() -> str:
    return (
        "You are the PIPELINE COMPANION for the Open-Write autonomous writing pipeline. "
        "You help the writer steer a running (or completed) novel-production pipeline in plain "
        "language. You know the pipeline's phase roadmap, the current cursor, the creative brief, "
        "and a compact summary of which artifacts already exist on disk.\n\n"
        "How to help:\n"
        "- Be concrete and grounded. Refer to the actual current phase, chapter, and brief.\n"
        "- If the writer asks for a change to the creative DIRECTION that should apply to future "
        "  output, propose a REVISED creative brief and emit it in a fenced block exactly like:\n"
        "    SUGGESTED_BRIEF:\n    <the new full creative brief>\n    :END\n"
        "  Keep the suggested brief complete and self-contained — it replaces the current one.\n"
        "- Never claim a phase passed or a file exists if the context doesn't say so.\n"
        "- Keep responses concise and actionable.\n"
    )


class PipelineChatResponse(BaseModel):
    reply: str
    suggested_instructions: str | None = None
    model_used: str


@router.post("/{project_id}/chat", response_model=PipelineChatResponse)
async def pipeline_chat(project_id: str, req: PipelineChatRequest,
                        current=Depends(auth.get_current_user)):
    project = _resolve_project(project_id, current["id"])
    snapshot = orchestrator.chat_context_snapshot(project)
    api_key, model_name, base_url = _resolve_call_model(req.model_id)

    lines = [
        "--- PIPELINE CONTEXT (machine snapshot, current as of this message) ---",
        json.dumps(snapshot, indent=2, ensure_ascii=False),
    ]
    if req.context_artifact:
        try:
            art = outputs.read_artifact(project, req.context_artifact, max_chars=12000)
            if art.get("exists"):
                body = _strip_control_tokens(art["content"])
                lines.append("--- VIEWED ARTIFACT (reference only) ---")
                lines.append(body)
                lines.append("--- END VIEWED ARTIFACT ---")
        except ValueError:
            pass
    if req.context_chapter is not None:
        lines.append(f"(The writer is focused on chapter {req.context_chapter}.)")
    lines.append("Answer the writer's latest message.")
    materials = {"role": "user", "content": "\n".join(lines)}
    conversation = [{"role": m.role, "content": m.content} for m in req.messages]
    messages = [materials] + conversation

    from app.ai.openrouter import run_chat
    try:
        reply = await run_chat(
            api_key=api_key, model_id=model_name, base_url=base_url,
            system_prompt=_pipeline_chat_system_prompt(), messages=messages,
            temperature=0.4,
        )
    except httpx.HTTPStatusError as e:
        raise _provider_exc(e)
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Could not reach the model provider: {e}")

    clean, suggested = _extract_suggested_brief(reply)
    return PipelineChatResponse(reply=clean, suggested_instructions=suggested,
                                model_used=model_name)
