"""
do_not_edit_detector.py — Detect when a critic declines its own span.

Operates on the full finding body (not a preview) at classification time.
Excludes quoted-text regions so manuscript prose doesn't trigger false positives.

Standalone module. No orchestrator imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# ── Quote patterns to exclude from decline detection ────────────────────────
# These mark regions of the finding body that contain manuscript text rather
# than the critic's own judgment.

_QUOTE_PATTERNS = [
    # Blockquotes: > "text" or > text
    re.compile(r'^>\s*.+$', re.MULTILINE),
    # Inline quotes: "text" or \u201c...\u201d
    re.compile(r'["\u201c].+?["\u201d]'),
    # Backtick quotes: `text`
    re.compile(r'`.+?`'),
    # Bold-quoted manuscript: **Text:** "..." or **Text:** > ...
    re.compile(r'\*\*Text:\*\*\s*.*$', re.MULTILINE),
    # L-ref quoted lines: **L119:** "text"
    re.compile(r'\*\*L\d+:?\*\*\s*:?\s*["\u201c].+?["\u201d]'),
    # Format A text block
    re.compile(r'\*\*Text:\*\*\s*["\u201c].+?["\u201d]', re.DOTALL),
]


def _strip_quotes(body: str) -> str:
    """Remove quoted-text regions from the finding body, leaving only the
    critic's own prose for decline-language detection."""
    result = body
    for pattern in _QUOTE_PATTERNS:
        result = pattern.sub('', result)
    return result


# ── Decline phrase patterns ─────────────────────────────────────────────────
# Derived from corpus scan (see docs/arrs/21 §Part 2).
# Each pattern is (regex, description).

_DECLINE_PHRASES = [
    # Direct "earns" language — the critic says the instance is justified
    (re.compile(r'\bearns?\s+(?:its|their|his|her)\b', re.I), "earns its/their"),
    (re.compile(r'\bearns?\s+(?:the|a|this)\b', re.I), "earns the/a/this"),

    # "Keep" language — the critic recommends keeping the instance
    (re.compile(r'\bkeep\s+(?:the|this|it|them|all)\b', re.I), "keep the/this"),
    (re.compile(r'\bstay\b(?!\s+(?:in|at|on|with))', re.I), "stay (as in keep)"),

    # "Effective/works" language — the critic says the instance works
    (re.compile(r'\beffective\s+(?:here|in|at|for)\b', re.I), "effective here/in"),
    (re.compile(r'\bworks?\s+(?:here|in|at|for|as)\b', re.I), "works here/in/as"),
    (re.compile(r'\bjustified\b', re.I), "justified"),
    (re.compile(r'\bdeliberate\b', re.I), "deliberate"),

    # "Retain" language
    (re.compile(r'\bretain\b', re.I), "retain"),
    (re.compile(r'\brecommend\s+(?:retaining|keeping)\b', re.I), "recommend retaining/keeping"),

    # "Earned" language — past tense
    (re.compile(r'\bearned\s+(?:its|their|his|her|the|a|this)\b', re.I), "earned its/their"),
    (re.compile(r'\bis\s+earned\b', re.I), "is earned"),

    # "Not a violation" / "not hedging" — the critic explicitly says it's fine
    (re.compile(r'\bnot\s+(?:a\s+)?(?:violation|hedging|a\s+default)\b', re.I), "not a violation/hedging"),
]


@dataclass(frozen=True)
class DeclineDetection:
    """Result of decline-language detection."""
    is_declined: bool
    phrases_found: list[str]     # Descriptions of matched phrases
    match_positions: list[int]   # Character offsets in the stripped body
    in_quoted_region: bool       # True if all matches were in quoted regions


def detect_decline(body: str, quoted_text: Optional[str] = None,
                   line_start: Optional[int] = None) -> DeclineDetection:
    """Detect whether a finding's body contains decline language.

    Args:
        body: The full finding body text.
        quoted_text: The finding's quoted excerpt (if any). Used to verify
            that decline language isn't inside the quoted manuscript text.
        line_start: The finding's line number. If provided, decline language
            must specifically reference this line (e.g., "L89 earns its list"
            only applies to the L89 finding, not to L7 in the same section).

    Returns:
        DeclineDetection with match details.
    """
    if not body or len(body.strip()) < 10:
        return DeclineDetection(
            is_declined=False, phrases_found=[], match_positions=[], in_quoted_region=False
        )

    # Strip quoted regions to get critic-only prose
    stripped = _strip_quotes(body)

    # Also strip the finding's own quoted_text if provided
    if quoted_text and len(quoted_text) > 10:
        stripped = stripped.replace(quoted_text, '')

    phrases_found = []
    match_positions = []

    for pattern, description in _DECLINE_PHRASES:
        for m in pattern.finditer(stripped):
            # If we have a line number, check that the decline language
            # specifically references this line. "L89 earns its list" only
            # applies to the L89 finding, not to L7 in the same section.
            if line_start is not None:
                # Look for a line reference near the match (within 100 chars)
                context_start = max(0, m.start() - 100)
                context_end = min(len(stripped), m.end() + 50)
                context = stripped[context_start:context_end]
                # Check if our line number is mentioned near the decline language
                line_ref_pattern = re.compile(
                    r'(?:^|\s|,)L?' + re.escape(str(line_start)) + r'(?:\s|,|$|[^0-9])',
                    re.MULTILINE
                )
                if not line_ref_pattern.search(context):
                    continue  # Decline language doesn't reference this line

            phrases_found.append(description)
            match_positions.append(m.start())

    # Check if all matches are in quoted regions (false positive)
    in_quoted = False
    if phrases_found and not stripped.strip():
        in_quoted = True
        phrases_found = []
        match_positions = []

    return DeclineDetection(
        is_declined=len(phrases_found) > 0,
        phrases_found=phrases_found,
        match_positions=match_positions,
        in_quoted_region=in_quoted,
    )
