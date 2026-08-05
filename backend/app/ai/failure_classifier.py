"""
failure_classifier.py — Standalone failure classification for OpenAI-compatible
provider responses.

No orchestrator dependencies. Input is an HTTP response, exception, or parsed
response body. Output is a FailureClass and a prescribed action.

Provider signals captured in docs/arrs/08-provider-signals.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class FailureClass(int, Enum):
    """The nine failure classes from the retry-policy spec."""
    OK = 0                # Not a failure — included for completeness
    NETWORK_TRANSIENT = 1
    RATE_LIMIT = 2
    SERVER_ERROR = 3
    AUTH_PAYMENT = 4
    REFUSAL = 5
    TRUNCATION = 6
    EMPTY_COMPLETION = 7
    MALFORMED_OUTPUT = 8
    QUALITY = 9


class Action(str, Enum):
    """Prescribed action per failure class."""
    CONTINUE = "continue"              # Success — proceed
    RETRY_SAME = "retry_same"          # Retry with same model/provider
    RETRY_WITH_BACKOFF = "retry_backoff"  # Retry with backoff schedule
    SWITCH_PROVIDER = "switch_provider"   # Switch to secondary provider
    SWITCH_THEN_HALT = "switch_then_halt" # One switch attempt, then halt
    HALT_IMMEDIATE = "halt_immediate"     # No retry, no switch — halt
    HALT_UNIT = "halt_unit"               # Halt this unit, record location
    ESCALATE_RETRY = "escalate_retry"     # Re-prompt with fault named


@dataclass(frozen=True)
class Classification:
    """Result of classifying a provider response."""
    failure_class: FailureClass
    action: Action
    detail: str
    retry_same_allowed: bool
    switch_allowed: bool
    max_attempts: int          # 0 = no retry
    backoff_schedule: list     # seconds per attempt, empty = no backoff
    retry_after: Optional[float] = None  # seconds from Retry-After header, if any


# ── Refusal content patterns ─────────────────────────────────────────────────
# ZAI returns finish_reason="stop" for refusals. The only detection signal is
# the content text. These patterns are conservative — they match explicit
# refusal language, not general hedging.
_REFUSAL_PATTERNS = [
    re.compile(r"i (?:cannot|can't|am unable to|'m not able to) fulfill this request", re.I),
    re.compile(r"i (?:cannot|can't|am not able to) (?:provide|assist with|help with) (?:this|that|these)", re.I),
    re.compile(r"i(?:'?m| am) (?:not (?:able|permitted|allowed|authorized)|(?:unable)) to", re.I),
    re.compile(r"my (?:safety |content )?(?:guidelines|policies|programming) (?:prohibit|prevent|restrict) me", re.I),
    re.compile(r"this (?:request|query) (?:violates|goes against)", re.I),
    re.compile(r"the request was rejected because", re.I),  # MiMo's refusal text
]


def _is_refusal_content(content: str) -> bool:
    """Heuristic check for refusal language in model output.

    Only used when finish_reason does not signal refusal (ZAI case).
    Conservative: matches explicit refusal phrases only.
    """
    if not content:
        return False
    # Check first 500 chars — refusals lead with the refusal statement.
    head = content[:500]
    return any(p.search(head) for p in _REFUSAL_PATTERNS)


def classify_response(
    http_status: int,
    finish_reason: Optional[str],
    content: Optional[str],
    error_body: Optional[dict] = None,
    retry_after_header: Optional[str] = None,
) -> Classification:
    """Classify an HTTP response from an OpenAI-compatible chat completions endpoint.

    Args:
        http_status: HTTP status code from the response.
        finish_reason: choices[0].finish_reason, or None if not available.
        content: choices[0].message.content, or None if not available.
            Distinguish from empty string — None means the field was absent,
            "" means the provider returned an empty string.
        error_body: The parsed JSON error body, if the response was an HTTP error.
        retry_after_header: The Retry-After header value, if present.

    Returns:
        Classification with failure class, action, and retry parameters.
    """
    # ── Class 4: Auth / payment (non-retryable HTTP errors) ──────────
    if http_status in (401, 402, 403):
        return Classification(
            failure_class=FailureClass.AUTH_PAYMENT,
            action=Action.HALT_IMMEDIATE,
            detail=f"HTTP {http_status}: authentication or payment error",
            retry_same_allowed=False,
            switch_allowed=False,
            max_attempts=0,
            backoff_schedule=[],
        )

    # ── Class 2: Rate limit ──────────────────────────────────────────
    if http_status == 429:
        retry_after = _parse_retry_after(retry_after_header)
        return Classification(
            failure_class=FailureClass.RATE_LIMIT,
            action=Action.RETRY_WITH_BACKOFF,
            detail=f"HTTP 429: rate limited" + (f" (Retry-After: {retry_after}s)" if retry_after else ""),
            retry_same_allowed=True,
            switch_allowed=True,
            max_attempts=3,
            backoff_schedule=[5, 15, 45],
            retry_after=retry_after,
        )

    # ── Class 3: Server error ────────────────────────────────────────
    if http_status in (500, 502, 503, 504):
        return Classification(
            failure_class=FailureClass.SERVER_ERROR,
            action=Action.RETRY_WITH_BACKOFF,
            detail=f"HTTP {http_status}: server error",
            retry_same_allowed=True,
            switch_allowed=True,
            max_attempts=3,
            backoff_schedule=[2, 6, 18],
        )

    # ── Other HTTP errors (400 with specific error codes) ────────────
    if http_status >= 400:
        # Check if it's a model-not-found error (class 8, not retryable)
        err_msg = ""
        if error_body and isinstance(error_body.get("error"), dict):
            err_msg = error_body["error"].get("message", "")
        return Classification(
            failure_class=FailureClass.MALFORMED_OUTPUT,
            action=Action.HALT_UNIT,
            detail=f"HTTP {http_status}: {err_msg or 'client error'}",
            retry_same_allowed=False,
            switch_allowed=False,
            max_attempts=0,
            backoff_schedule=[],
        )

    # ── From here: HTTP 200 — inspect finish_reason and content ──────

    # ── Class 6: Truncation ──────────────────────────────────────────
    if finish_reason == "length":
        return Classification(
            failure_class=FailureClass.TRUNCATION,
            action=Action.HALT_UNIT,
            detail="finish_reason=length: output truncated",
            retry_same_allowed=False,
            switch_allowed=False,
            max_attempts=0,
            backoff_schedule=[],
        )

    # ── Class 5: Refusal (by finish_reason — MiMo path) ─────────────
    if finish_reason == "content_filter":
        return Classification(
            failure_class=FailureClass.REFUSAL,
            action=Action.SWITCH_THEN_HALT,
            detail="finish_reason=content_filter: provider refused the request",
            retry_same_allowed=False,
            switch_allowed=True,
            max_attempts=1,
            backoff_schedule=[],
        )

    # ── Class 5: Refusal (by content inspection — ZAI path) ─────────
    if finish_reason == "stop" and content and _is_refusal_content(content):
        return Classification(
            failure_class=FailureClass.REFUSAL,
            action=Action.SWITCH_THEN_HALT,
            detail="refusal detected by content pattern matching",
            retry_same_allowed=False,
            switch_allowed=True,
            max_attempts=1,
            backoff_schedule=[],
        )

    # ── Class 7: Empty completion ────────────────────────────────────
    # content=None means the field was absent (provider bug).
    # content="" means the provider explicitly returned empty string.
    # Both count as empty when finish_reason is "stop".
    if finish_reason == "stop" and (content is None or content == ""):
        return Classification(
            failure_class=FailureClass.EMPTY_COMPLETION,
            action=Action.SWITCH_THEN_HALT,
            detail="finish_reason=stop but content is empty",
            retry_same_allowed=True,
            switch_allowed=True,
            max_attempts=1,
            backoff_schedule=[],
        )

    # ── Class 8: Malformed output (structural check deferred) ────────
    # The structural check (expected sections present, parseable) is done
    # by the caller, not here. This classifier handles HTTP/transport signals.
    # If the caller detects structural malformation, it should call
    # classify_malformed() instead.

    # ── OK ───────────────────────────────────────────────────────────
    return Classification(
        failure_class=FailureClass.OK,
        action=Action.CONTINUE,
        detail="success",
        retry_same_allowed=False,
        switch_allowed=False,
        max_attempts=0,
        backoff_schedule=[],
    )


def classify_exception(exc: Exception) -> Classification:
    """Classify a Python exception raised during an HTTP call.

    Args:
        exc: The exception (httpx.RequestError, httpx.TimeoutException, etc.)

    Returns:
        Classification with failure class and action.
    """
    import httpx

    # ── Network transient ────────────────────────────────────────────
    if isinstance(exc, httpx.TimeoutException):
        return Classification(
            failure_class=FailureClass.NETWORK_TRANSIENT,
            action=Action.RETRY_WITH_BACKOFF,
            detail=f"timeout: {type(exc).__name__}: {exc}",
            retry_same_allowed=True,
            switch_allowed=False,
            max_attempts=5,
            backoff_schedule=[2, 4, 8, 300, 30],
        )

    if isinstance(exc, httpx.RequestError):
        return Classification(
            failure_class=FailureClass.NETWORK_TRANSIENT,
            action=Action.RETRY_WITH_BACKOFF,
            detail=f"network error: {type(exc).__name__}: {exc}",
            retry_same_allowed=True,
            switch_allowed=False,
            max_attempts=5,
            backoff_schedule=[2, 4, 8, 300, 30],
        )

    # ── Unknown exception — halt ─────────────────────────────────────
    return Classification(
        failure_class=FailureClass.NETWORK_TRANSIENT,
        action=Action.HALT_IMMEDIATE,
        detail=f"unclassified exception: {type(exc).__name__}: {exc}",
        retry_same_allowed=False,
        switch_allowed=False,
        max_attempts=0,
        backoff_schedule=[],
    )


def classify_malformed(detail: str) -> Classification:
    """Classify a structural malformation detected by the caller.

    Used when the model returned HTTP 200 + valid JSON, but the expected
    structure is missing (e.g., bible reply won't split, missing sections,
    unparseable verdict).
    """
    return Classification(
        failure_class=FailureClass.MALFORMED_OUTPUT,
        action=Action.ESCALATE_RETRY,
        detail=f"malformed output: {detail}",
        retry_same_allowed=True,
        switch_allowed=False,
        max_attempts=2,
        backoff_schedule=[],
    )


def _parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """Parse a Retry-After header value, clamped to 120s max."""
    if not header_value:
        return None
    try:
        seconds = float(header_value)
        return min(seconds, 120.0)
    except (ValueError, TypeError):
        return None
