"""
locate_and_verify.py — Locator-then-verify pattern for surgical edits.

Given a finding's quoted_text (possibly truncated by the parser), locate the
full passage in the chapter and verify the pre_image against the actual text.

This is the bridge between the parser's truncated quotes and the edit_applier's
exact pre_image requirement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LocateResult:
    """Outcome of locating a quoted span in chapter text."""
    found: bool
    start: Optional[int]          # Chapter offset (0-indexed)
    end: Optional[int]            # Chapter offset (exclusive)
    pre_image: Optional[str]      # Full text at [start:end]
    match_type: Optional[str]     # "exact", "prefix", "multi", "none"
    ambiguity_count: int = 0      # How many prefix matches found (0 or 1 = unambiguous)


def _find_sentence_boundary(text: str, offset: int, direction: int = 1) -> int:
    """Find the nearest sentence boundary from offset.
    
    direction: 1 = forward, -1 = backward.
    Returns the offset of the boundary character.
    """
    # Sentence-ending punctuation
    terminators = set('.!?\u2026')  # period, exclamation, question, ellipsis
    # Also treat paragraph breaks as boundaries
    para_breaks = set('\n\n')
    
    i = offset
    while 0 <= i < len(text):
        if text[i] in terminators:
            # Check it's not an abbreviation (e.g., "Mr." "U.S.")
            # Simple heuristic: if followed by space or end, it's a sentence end
            if i + 1 >= len(text) or text[i + 1] in ' \n\t':
                return i + 1 if direction == 1 else i
        if direction == 1 and i + 1 < len(text) and text[i:i+2] == '\n\n':
            return i + 2
        if direction == -1 and i >= 1 and text[i-1:i+1] == '\n\n':
            return i
        i += direction
    return len(text) if direction == 1 else 0


def locate_quote(chapter: str, quoted_text: str) -> LocateResult:
    """Locate a (possibly truncated) quote in the chapter text.
    
    Strategy:
    1. Try exact match first.
    2. If not found, try prefix match — the quote is a truncated excerpt.
    3. If prefix match is ambiguous (multiple matches), report ambiguity.
    4. For prefix matches, extend to the nearest sentence boundary to get
       the full pre_image.
    """
    if not quoted_text or len(quoted_text.strip()) < 5:
        return LocateResult(found=False, start=None, end=None, pre_image=None,
                           match_type="none", ambiguity_count=0)
    
    clean_quote = quoted_text.strip()
    
    # Strategy 1: Exact match
    idx = chapter.find(clean_quote)
    if idx >= 0:
        return LocateResult(
            found=True, start=idx, end=idx + len(clean_quote),
            pre_image=clean_quote, match_type="exact", ambiguity_count=1
        )
    
    # Strategy 2: Prefix match — the quote is truncated
    # Use progressively shorter prefixes until we find a match
    # Start with the full quote, then try 80%, 60%, 40%, 30 chars
    prefix_lengths = [
        len(clean_quote),
        int(len(clean_quote) * 0.8),
        int(len(clean_quote) * 0.6),
        int(len(clean_quote) * 0.4),
        min(30, len(clean_quote)),
        min(20, len(clean_quote)),
    ]
    
    for plen in prefix_lengths:
        if plen < 15:  # Don't try very short prefixes — too ambiguous
            break
        prefix = clean_quote[:plen]
        
        # Count occurrences of this prefix
        occurrences = []
        search_start = 0
        while True:
            idx = chapter.find(prefix, search_start)
            if idx < 0:
                break
            occurrences.append(idx)
            search_start = idx + 1
        
        if len(occurrences) == 1:
            # Unambiguous prefix match — extend to sentence boundary
            start = occurrences[0]
            # Find the end: extend forward from the prefix match to a sentence boundary
            # The full passage likely extends beyond the truncated quote
            end_candidate = start + len(clean_quote)  # At minimum, the full quote length
            # Try to find a sentence boundary within a reasonable range
            search_end = min(start + len(clean_quote) * 3, len(chapter))
            # Look for sentence end
            boundary = _find_sentence_boundary(chapter, end_candidate, direction=1)
            if boundary <= search_end:
                end = boundary
            else:
                end = end_candidate
            
            pre_image = chapter[start:end]
            return LocateResult(
                found=True, start=start, end=end, pre_image=pre_image,
                match_type="prefix", ambiguity_count=1
            )
        
        if len(occurrences) > 1:
            # Multiple prefix matches — ambiguous
            # Use the first one but flag ambiguity
            start = occurrences[0]
            end_candidate = start + len(clean_quote)
            search_end = min(start + len(clean_quote) * 3, len(chapter))
            boundary = _find_sentence_boundary(chapter, end_candidate, direction=1)
            if boundary <= search_end:
                end = boundary
            else:
                end = end_candidate
            
            pre_image = chapter[start:end]
            return LocateResult(
                found=True, start=start, end=end, pre_image=pre_image,
                match_type="multi", ambiguity_count=len(occurrences)
            )
    
    # Strategy 3: Try line-based location using line_start from the finding
    # (not implemented here — requires the finding dict)
    
    return LocateResult(found=False, start=None, end=None, pre_image=None,
                       match_type="none", ambiguity_count=0)
