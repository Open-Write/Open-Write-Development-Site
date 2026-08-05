"""
reviser_router.py — Dispatch a parsed+classified finding to a handler.

The router is the first stage after classification. It decides which handler
receives a finding based on its quadrant (surgical/structural × instance/pattern)
and filters out findings that should not be dispatched (do-not-edit, stale,
unclassifiable).

No LLM calls. No orchestrator imports. Standalone module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from app.pipeline.reviser_tools.finding_classifier import Classification
from app.pipeline.reviser_tools.do_not_edit_detector import detect_decline
from app.pipeline.reviser_tools.locate_and_verify import locate_quote, LocateResult


# ── Handler interface ────────────────────────────────────────────────────────

class FindingHandler(Protocol):
    """Interface for a finding handler (reviser).

    A handler receives a classified finding with its section context and
    chapter text, and returns a list of proposed edits plus a disposition.
    """
    async def handle(self, finding: "RoutedFinding") -> "HandlerResult": ...


@dataclass(frozen=True)
class RoutedFinding:
    """A finding ready for dispatch: parsed + classified + located + context."""
    finding: dict                   # The parsed finding dict
    classification: Classification  # From finding_classifier
    locate_result: Optional[LocateResult]  # From locate_quote (surgical only)
    chapter_text: str               # Full chapter text
    body_full: str                  # Full finding body (section context)


@dataclass(frozen=True)
class HandlerResult:
    """Output from a handler."""
    proposed_edits: list[dict]      # List of edit specs (empty for null handler)
    disposition: str                # "attempted", "declined", "unable"
    reason: str                     # Why this disposition


# ── Dispatch result ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DispatchResult:
    """Outcome of dispatching one finding."""
    finding_id: str
    quadrant: str                   # "surgical_instance", "surgical_pattern",
                                    # "structural", "unclassifiable", "skipped"
    handler_name: Optional[str]     # Which handler received it, or None if skipped
    skip_reason: Optional[str]      # Why skipped (do_not_edit, stale, etc.)
    handler_result: Optional[HandlerResult] = None


# ── Skip reasons ─────────────────────────────────────────────────────────────

SKIP_DO_NOT_EDIT = "do_not_edit"
SKIP_STALE = "stale_quote"
SKIP_UNCLASSIFIABLE = "unclassifiable"
SKIP_NO_SPAN = "no_span"


# ── The router ───────────────────────────────────────────────────────────────

# Handler registry: quadrant name → handler instance
_HANDLER_REGISTRY: dict[str, FindingHandler] = {}


def register_handler(quadrant: str, handler: FindingHandler) -> None:
    """Register a handler for a quadrant."""
    _HANDLER_REGISTRY[quadrant] = handler


def get_handler(quadrant: str) -> Optional[FindingHandler]:
    """Get the handler for a quadrant, or None if not registered."""
    return _HANDLER_REGISTRY.get(quadrant)


def _derive_finding_id(finding: dict) -> str:
    """Derive a stable finding ID from parsed fields."""
    import hashlib
    ch = finding.get("chapter") or 0
    critic = (finding.get("critic_type") or "unknown").replace("_", "-")
    quote = finding.get("quoted_text")
    if quote and len(quote) > 10:
        import re
        normalized = quote.strip()
        normalized = re.sub(r'\s+', ' ', normalized)
        normalized = normalized.lower()
        normalized = normalized.rstrip('.,;:!?')
        normalized = normalized.strip('"\u201c\u201d')
        content = f"{ch}:{critic}:{normalized}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    num = finding.get("num") or 0
    line = finding.get("line_start") or 0
    return f"pos-{ch:03d}-{critic}-{num:03d}-L{line}"


def _is_stale(finding: dict, chapter_text: str) -> bool:
    """Check if a finding's quoted text appears in the current chapter."""
    quote = finding.get("quoted_text")
    if not quote or len(quote.strip()) < 10:
        return False  # No quote to verify — not stale (but might be unclassifiable)
    loc = locate_quote(chapter_text, quote)
    return not loc.found


def dispatch_finding(
    finding: dict,
    classification: Classification,
    chapter_text: str,
) -> DispatchResult:
    """Dispatch a single classified finding to the appropriate handler.

    Routing rules:
    1. do_not_edit → skip (never dispatched)
    2. stale quote → skip
    3. unclassifiable → skip
    4. surgical+instance → surgical_instance handler
    5. surgical+pattern → surgical_pattern handler
    6. structural → structural handler
    7. unclassified → unclassifiable (skip)
    """
    finding_id = _derive_finding_id(finding)

    # ── Filter: do_not_edit ──────────────────────────────────────────
    body_full = finding.get("body_full") or finding.get("body_preview") or ""
    quoted = finding.get("quoted_text")
    line_start = finding.get("line_start")
    decline = detect_decline(body_full, quoted, line_start)
    if decline.is_declined:
        return DispatchResult(
            finding_id=finding_id,
            quadrant="skipped",
            handler_name=None,
            skip_reason=SKIP_DO_NOT_EDIT,
        )

    # ── Filter: stale quote ──────────────────────────────────────────
    if classification.span_class == "surgical" and _is_stale(finding, chapter_text):
        return DispatchResult(
            finding_id=finding_id,
            quadrant="skipped",
            handler_name=None,
            skip_reason=SKIP_STALE,
        )

    # ── Route by quadrant ────────────────────────────────────────────
    span = classification.span_class
    scope = classification.scope_class

    if span == "unclassifiable":
        return DispatchResult(
            finding_id=finding_id,
            quadrant="unclassifiable",
            handler_name=None,
            skip_reason=SKIP_UNCLASSIFIABLE,
        )

    if span == "structural":
        handler = get_handler("structural")
        return DispatchResult(
            finding_id=finding_id,
            quadrant="structural",
            handler_name="structural" if handler else None,
            skip_reason=SKIP_NO_SPAN if not handler else None,
        )

    # span == "surgical"
    if scope == "instance":
        quadrant = "surgical_instance"
    elif scope == "pattern":
        quadrant = "surgical_pattern"
    else:
        return DispatchResult(
            finding_id=finding_id,
            quadrant="unclassifiable",
            handler_name=None,
            skip_reason=SKIP_UNCLASSIFIABLE,
        )

    handler = get_handler(quadrant)
    if handler is None:
        return DispatchResult(
            finding_id=finding_id,
            quadrant=quadrant,
            handler_name=None,
            skip_reason=f"no handler registered for {quadrant}",
        )

    # Build the routed finding with section context
    locate_result = None
    if finding.get("quoted_text") and chapter_text:
        locate_result = locate_quote(chapter_text, finding["quoted_text"])

    routed = RoutedFinding(
        finding=finding,
        classification=classification,
        locate_result=locate_result,
        chapter_text=chapter_text,
        body_full=body_full,
    )

    # Call the handler (synchronous wrapper for the async protocol)
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async context — create a new task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, handler.handle(routed)).result()
        else:
            result = loop.run_until_complete(handler.handle(routed))
    except RuntimeError:
        result = asyncio.run(handler.handle(routed))

    return DispatchResult(
        finding_id=finding_id,
        quadrant=quadrant,
        handler_name=quadrant,
        skip_reason=None,
        handler_result=result,
    )


def dispatch_findings(
    findings: list[dict],
    classifications: list[Classification],
    chapter_text: str,
) -> list[DispatchResult]:
    """Dispatch a list of findings."""
    return [
        dispatch_finding(f, c, chapter_text)
        for f, c in zip(findings, classifications)
    ]
