"""
finding_classifier.py — Classify parsed findings along two orthogonal axes:
  1. Surgical / Structural / Unclassifiable (span availability)
  2. Instance / Pattern / Unclassified (scope of the finding)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import re


_PATTERN_TYPE_KEYWORDS = [
    'monotony', 'density', 'polysyndeton', 'triplet', 'default rhythm',
    'saturated', 'chains', 'pattern', 'recurring', 'throughout',
]

_INSTANCE_CRITIC_TYPES = {'show_dont_tell', 'voice', 'palette', 'continuity'}

_INSTANCE_PROSE_AUDIT_TYPES = {
    'expletive', 'existential expletive',
    'not just x but y', 'not x but y', 'in a way that z',
    'construction', 'banned construction',
}


@dataclass(frozen=True)
class Classification:
    finding: dict
    span_class: str
    scope_class: str
    span_reason: str
    scope_reason: str


def _has_pattern_language(finding: dict) -> bool:
    ftype = (finding.get("type") or "").lower()
    return any(kw in ftype for kw in _PATTERN_TYPE_KEYWORDS)


def _has_earns_language(finding: dict) -> bool:
    """Detect decline language in the finding body.

    Uses body_full (the complete finding body) rather than body_preview
    (300 chars), because decline language typically appears in the
    recommendation section which is beyond the 300-char cutoff.

    Passes line_start so that decline language that specifically references
    a different line (e.g., "L89 earns its list" when this finding is L7)
    doesn't trigger a false positive.
    """
    from app.pipeline.reviser_tools.do_not_edit_detector import detect_decline
    body = finding.get("body_full") or finding.get("body_preview") or ""
    quoted = finding.get("quoted_text")
    line_start = finding.get("line_start")
    detection = detect_decline(body, quoted, line_start)
    return detection.is_declined


def _is_prose_audit_instance(finding: dict) -> bool:
    ftype = (finding.get("type") or "").lower()
    return any(kw in ftype for kw in _INSTANCE_PROSE_AUDIT_TYPES)


def _multiple_line_refs_in_parent(finding: dict) -> bool:
    body = finding.get("body_preview", "")
    l_refs = re.findall(r'\*\*L(\d+)', body)
    if len(l_refs) > 1:
        return True
    if re.search(r'\d+\s+(?:lines?|sentences?|instances?)\s+(?:contain|with|of)', body):
        return True
    return False


def classify_finding(finding: dict, chapter_content: str = "") -> Classification:
    has_line = finding.get("line_start") is not None
    has_quote = finding.get("quoted_text") is not None and len(finding.get("quoted_text", "")) > 10
    is_clean = finding.get("is_clean", False)
    critic_type = finding.get("critic_type") or ""

    # Axis 1: Span availability
    if is_clean:
        span_class = "structural"
        span_reason = "clean passage"
    elif has_quote and chapter_content:
        idx = chapter_content.find(finding["quoted_text"])
        if idx == -1:
            truncated = finding["quoted_text"].rstrip('.').rstrip()
            if len(truncated) > 20:
                idx = chapter_content.find(truncated[:50])
        if idx >= 0:
            span_class = "surgical"
            span_reason = "verified quote"
        else:
            span_class = "structural"
            span_reason = "stale quote"
    elif has_quote and not chapter_content:
        span_class = "surgical"
        span_reason = "unverified quote"
    elif has_line and not has_quote:
        span_class = "unclassifiable"
        span_reason = "line only"
    else:
        span_class = "structural"
        span_reason = "no span"

    # Axis 2: Instance vs. Pattern (only for surgical)
    if span_class != "surgical":
        return Classification(
            finding=finding, span_class=span_class, scope_class="unclassified",
            span_reason=span_reason, scope_reason=f"not surgical",
        )

    if _multiple_line_refs_in_parent(finding):
        return Classification(
            finding=finding, span_class=span_class, scope_class="pattern",
            span_reason=span_reason, scope_reason="multiple L-refs in parent",
        )

    if _has_pattern_language(finding):
        return Classification(
            finding=finding, span_class=span_class, scope_class="pattern",
            span_reason=span_reason, scope_reason="pattern language in type",
        )

    if _has_earns_language(finding):
        return Classification(
            finding=finding, span_class=span_class, scope_class="pattern",
            span_reason=span_reason, scope_reason="critic says keep instance",
        )

    if critic_type in _INSTANCE_CRITIC_TYPES:
        return Classification(
            finding=finding, span_class=span_class, scope_class="instance",
            span_reason=span_reason, scope_reason=f"instance-oriented critic",
        )

    if critic_type == "prose_audit":
        if _is_prose_audit_instance(finding):
            return Classification(
                finding=finding, span_class=span_class, scope_class="instance",
                span_reason=span_reason, scope_reason="prose_audit instance",
            )
        return Classification(
            finding=finding, span_class=span_class, scope_class="pattern",
            span_reason=span_reason, scope_reason="prose_audit pattern",
        )

    if critic_type == "naturalism":
        return Classification(
            finding=finding, span_class=span_class, scope_class="pattern",
            span_reason=span_reason, scope_reason="naturalism default",
        )

    return Classification(
        finding=finding, span_class=span_class, scope_class="unclassified",
        span_reason=span_reason, scope_reason="insufficient signal",
    )


def classify_findings(findings: list[dict], chapter_content: str = "") -> list[Classification]:
    return [classify_finding(f, chapter_content) for f in findings]
