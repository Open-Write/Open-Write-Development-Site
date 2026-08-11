"""
classifier.py — Step 1: Failure classification for generation attempts.

Every generation attempt is assigned to exactly one class. Only Class II and
Class III increment the evaluator trigger counter. Class I is infrastructure
failure that auto-retries silently.

Classification is deterministic and unit-tested. The specific subtype for
Class II is passed to the Evaluator as evidence.

Classes:
  I   — Infrastructure. Auto-retry with backoff. Does NOT increment counter.
  II  — Degenerate. Well-formed call, pathological output. Increments counter.
  III — Proper failure. Complete, coherent, failed a quality gate. Increments.

Special case:
  target_underset — finish_reason: length when output is at/above target.
                    Not a failure. Log and surface. Do not retry, do not count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class FailureClass(str, Enum):
    INFRASTRUCTURE = "I"
    DEGENERATE = "II"
    PROPER = "III"
    TARGET_UNDERSET = "target_underset"  # not a failure


class DegenerateSubtype(str, Enum):
    REPETITION = "repetition"
    META_COMMENTARY = "meta_commentary"
    SUMMARY_COLLAPSE = "summary_collapse"
    BRIEF_RESTATEMENT = "brief_restatement"


@dataclass
class Classification:
    """Result of classifying a generation attempt."""
    failure_class: FailureClass
    subtype: Optional[DegenerateSubtype] = None  # only for Class II
    reason: str = ""
    increments_counter: bool = False
    stripped_artifacts: list[dict] = field(default_factory=list)
    # What was stripped and why — from strip_artifacts(). Only populated when
    # the stripping step provides this information (Class II signal).


# ── Infrastructure signals (Class I) ─────────────────────────────────────────

# These are checked FIRST. If any match, the attempt is Class I regardless
# of output quality — the infrastructure failed, not the generation.

_EMPTY_OR_WHITESPACE = re.compile(r"^\s*$")

# Preamble / process leakage patterns that indicate the model is narrating
# its own process rather than producing prose.
_PREAMBLE_PATTERNS = [
    re.compile(r"^\s*(?:i(?:'ll| will| need to)|(?:let me|here(?:'s| is))|before (?:writing|i|we)|reading completed|i need to read)", re.IGNORECASE),
    re.compile(r"^\s*(?:certainly|of course|here is|i'll now|i'll continue|continuing with|moving on to|let's proceed)", re.IGNORECASE),
    re.compile(r"^\s*(?:---\s*(?:BEGIN|FINDING|CRITIC|PROCESS|META))", re.IGNORECASE),
]

# Meta-commentary that fills the output instead of prose.
_META_COMMENTARY_PATTERNS = [
    re.compile(r"^\s*(?:here (?:is|are) the (?:chapter|scene|section|prose|text|story|draft))", re.IGNORECASE),
    re.compile(r"^\s*(?:i'll (?:now |)write|let me (?:write|compose|draft))", re.IGNORECASE),
    re.compile(r"^\s*(?:this (?:chapter|scene|section) (?:will|should|needs))", re.IGNORECASE),
    re.compile(r"^\s*(?:word count|target)[:\s]", re.IGNORECASE),
]

# Refusal / content-filter patterns.
_REFUSAL_PATTERNS = [
    re.compile(r"^\s*(?:i (?:can't|cannot|am unable|'m unable) (?:help|assist|write|create|generate|produce))", re.IGNORECASE),
    re.compile(r"^\s*(?:as an? ai|i (?:don't|do not) (?:have|possess|feel))", re.IGNORECASE),
    re.compile(r"^\s*(?:i('m| am) (?:sorry|afraid|not able))", re.IGNORECASE),
    re.compile(r"(?:this (?:request|content|material) (?:violates|goes against|is against))", re.IGNORECASE),
]


def classify(
    output: str,
    word_count: int,
    word_target: int,
    word_floor: int,
    finish_reason: Optional[str] = None,
    stripped_artifact_types: Optional[list[str]] = None,
    http_status: Optional[int] = None,
    error_message: Optional[str] = None,
) -> Classification:
    """Classify a generation attempt.

    Args:
        output: The model's raw output text (after extraction, before stripping).
        word_count: Word count of the clean prose (after stripping).
        word_target: The per-chapter word target.
        word_floor: The minimum acceptable word count.
        finish_reason: The model's finish_reason ("stop", "length", etc.).
        stripped_artifact_types: What strip_artifacts() removed. Types are
            strings like "meta_commentary", "process_leakage", "headers",
            "tool_calls", "json_params".
        http_status: HTTP status code if an HTTP error occurred.
        error_message: Error message if an exception occurred.

    Returns:
        Classification with the assigned class, subtype, reason, and whether
        this increments the evaluator trigger counter.
    """
    # ── Class I: Infrastructure failures (check first) ───────────────────

    # HTTP errors, timeouts, connection failures
    if http_status is not None:
        if http_status in (429, 502, 503, 504):
            return Classification(
                failure_class=FailureClass.INFRASTRUCTURE,
                reason=f"HTTP {http_status}: transient provider error",
            )
        if http_status in (401, 402):
            return Classification(
                failure_class=FailureClass.INFRASTRUCTURE,
                reason=f"HTTP {http_status}: auth/payment error",
            )

    # Connection/timeout errors
    if error_message:
        err_lower = error_message.lower()
        if any(kw in err_lower for kw in ("timeout", "timed out", "connection", "connect")):
            return Classification(
                failure_class=FailureClass.INFRASTRUCTURE,
                reason=f"Connection/timeout error: {error_message[:100]}",
            )

    # Empty or whitespace-only response
    if not output or _EMPTY_OR_WHITESPACE.match(output):
        return Classification(
            failure_class=FailureClass.INFRASTRUCTURE,
            reason="Empty or whitespace-only response",
        )

    # Refusal / content-filter stop
    for pat in _REFUSAL_PATTERNS:
        if pat.search(output[:500]):
            return Classification(
                failure_class=FailureClass.INFRASTRUCTURE,
                reason="Model refusal or content-filter stop",
            )

    # finish_reason: length when output is below target → Class I (truncation)
    if finish_reason == "length" and word_count < word_target * 0.9:
        return Classification(
            failure_class=FailureClass.INFRASTRUCTURE,
            reason=f"Truncated at max_tokens: {word_count} words < {word_target} target",
        )

    # ── Special case: target_underset (not a failure) ────────────────────

    if finish_reason == "length" and word_count >= word_target * 0.9:
        return Classification(
            failure_class=FailureClass.TARGET_UNDERSET,
            reason=f"finish_reason=length but output at/above target ({word_count} >= {word_target * 0.9:.0f}). "
                   f"Writer wanted more room. Consider raising max_tokens.",
        )

    # ── Class II: Degenerate output (well-formed call, pathological result) ──

    # Check stripped artifact types for Class II signals
    if stripped_artifact_types:
        meta_types = {"meta_commentary", "process_leakage"}
        stripped_meta = [t for t in stripped_artifact_types if t in meta_types]
        if stripped_meta:
            return Classification(
                failure_class=FailureClass.DEGENERATE,
                subtype=DegenerateSubtype.META_COMMENTARY,
                reason=f"Output contained meta-commentary/process leakage that was stripped: {stripped_meta}",
                increments_counter=True,
                stripped_artifacts=[{"type": t} for t in stripped_meta],
            )

    # Preamble leak (model narrating its own process)
    if output:
        first_500 = output[:500]
        for pat in _PREAMBLE_PATTERNS:
            if pat.search(first_500):
                return Classification(
                    failure_class=FailureClass.DEGENERATE,
                    subtype=DegenerateSubtype.META_COMMENTARY,
                    reason=f"Output begins with process narration, not prose",
                    increments_counter=True,
                )

    # Meta-commentary replacing prose
    if output:
        first_300 = output[:300]
        for pat in _META_COMMENTARY_PATTERNS:
            if pat.search(first_300):
                return Classification(
                    failure_class=FailureClass.DEGENERATE,
                    subtype=DegenerateSubtype.META_COMMENTARY,
                    reason=f"Output is meta-commentary about the task, not prose",
                    increments_counter=True,
                )

    # Repetition loop (n-gram repetition above threshold)
    rep_score = _compute_repetition_score(output, n=8)
    if rep_score > 0.4:
        return Classification(
            failure_class=FailureClass.DEGENERATE,
            subtype=DegenerateSubtype.REPETITION,
            reason=f"Repetition score {rep_score:.2f} exceeds 0.4 threshold (8-gram)",
            increments_counter=True,
        )

    # ── Class III: Proper failure (coherent, well-formed, below floor) ────

    if word_count < word_floor:
        return Classification(
            failure_class=FailureClass.PROPER,
            reason=f"Coherent output but {word_count} words below floor of {word_floor}",
            increments_counter=True,
        )

    # If we get here, the output passed the quality gate. This shouldn't
    # happen if the caller is using the classifier correctly (only called
    # on failed attempts), but handle gracefully.
    return Classification(
        failure_class=FailureClass.PROPER,
        reason=f"Output ({word_count} words) did not pass quality gate",
        increments_counter=True,
    )


def classify_from_stripped_info(
    output: str,
    word_count: int,
    word_target: int,
    word_floor: int,
    clean_word_count: int,
    stripped_count: int,
    stripped_types: list[str],
    finish_reason: Optional[str] = None,
) -> Classification:
    """Classify using information from _is_usable_prose's stripping step.

    This is the integration point with B2 — capture what was stripped and why,
    and pass it to the classifier as structured data.

    Args:
        output: Raw model output.
        word_count: Word count before stripping.
        word_target: Per-chapter word target.
        word_floor: Minimum acceptable word count.
        clean_word_count: Word count after stripping.
        stripped_count: Number of words stripped.
        stripped_types: What was stripped (e.g. ["meta_commentary", "headers"]).
        finish_reason: Model's finish_reason.

    Returns:
        Classification.
    """
    return classify(
        output=output,
        word_count=clean_word_count,
        word_target=word_target,
        word_floor=word_floor,
        finish_reason=finish_reason,
        stripped_artifact_types=stripped_types,
    )


# ── Repetition score (exposed for metrics module) ────────────────────────────

def _compute_repetition_score(text: str, n: int = 8) -> float:
    """Compute max n-gram repetition ratio.

    Returns the ratio of the most-repeated n-gram to total n-grams.
    A score above 0.4 indicates a repetition loop.
    """
    if not text or len(text) < n * 2:
        return 0.0

    words = text.lower().split()
    if len(words) < n:
        return 0.0

    ngrams: dict[tuple[str, ...], int] = {}
    for i in range(len(words) - n + 1):
        gram = tuple(words[i:i + n])
        ngrams[gram] = ngrams.get(gram, 0) + 1

    if not ngrams:
        return 0.0

    total = sum(ngrams.values())
    max_count = max(ngrams.values())
    return max_count / total
