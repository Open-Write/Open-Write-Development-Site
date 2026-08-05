"""
edit_applier.py — Apply structured span replacements to text content.

Standalone module. No orchestrator imports, no settings. Ports across builds
by copying, same as failure_classifier.py.

Input: file content (str) plus a list of Edit operations.
Output: modified content + per-edit ApplyResult (applied or rejected).

Design decisions (documented in docs/arrs/17-edit-application.md):
  - Offsets are Python string indices (0-indexed, UTF-16 code unit on CPython
    which uses UCS-2/UCS-4 internally). This matches how Python's str[slice]
    works. NOT UTF-8 byte offsets, NOT line:column.
  - Edits apply in reverse-offset order so earlier edits don't shift later ones.
  - pre_image is verified before applying — catches stale revisers.
  - Overlapping edits are rejected, not merged.
  - Line endings and encoding are the caller's responsibility (this module
    operates on str, not bytes).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Edit:
    """A single span replacement."""
    start: int          # Python string index (0-indexed)
    end: int            # Python string index (exclusive)
    replacement: str    # Text to insert in place of content[start:end]
    pre_image: str      # Expected text at content[start:end]; verified before apply
    note_id: str = ""   # Optional: links to a critic finding ID
    reason: str = ""    # Optional: human-readable reason


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of applying one Edit."""
    edit: Edit
    applied: bool
    rejection_reason: Optional[str] = None


@dataclass(frozen=True)
class PatchResult:
    """Outcome of applying a list of Edits."""
    content: str                    # Modified content (or original if all rejected)
    results: list[ApplyResult]      # Per-edit outcomes
    applied_count: int
    rejected_count: int


def _check_overlap(edits: list[Edit]) -> Optional[str]:
    """Return an error message if any edits overlap, else None."""
    sorted_edits = sorted(edits, key=lambda e: (e.start, e.end))
    for i in range(len(sorted_edits) - 1):
        a = sorted_edits[i]
        b = sorted_edits[i + 1]
        if a.end > b.start:
            return (
                f"Overlapping edits: edit at [{a.start}:{a.end}] "
                f"overlaps edit at [{b.start}:{b.end}]"
            )
    return None


def _validate_edit(edit: Edit, content: str) -> Optional[str]:
    """Return a rejection reason if the edit is invalid, else None."""
    if edit.start < 0:
        return f"Negative start offset: {edit.start}"
    if edit.end < edit.start:
        return f"End ({edit.end}) before start ({edit.start})"
    if edit.end > len(content):
        return (
            f"End offset {edit.end} exceeds content length {len(content)}"
        )
    actual = content[edit.start:edit.end]
    if actual != edit.pre_image:
        # Truncate for readability
        actual_preview = actual[:80] + ("..." if len(actual) > 80 else "")
        expected_preview = edit.pre_image[:80] + ("..." if len(edit.pre_image) > 80 else "")
        return (
            f"pre_image mismatch at [{edit.start}:{edit.end}]. "
            f"Expected: {expected_preview!r}. "
            f"Actual:   {actual_preview!r}"
        )
    return None


def apply_edits(content: str, edits: list[Edit]) -> PatchResult:
    """Apply a list of edits to content.

    Edits are applied in reverse-offset order so that earlier edits don't
    shift the offsets of later edits. Overlapping edits are rejected wholesale.
    Each edit's pre_image is verified before application.

    Args:
        content: The full text content of the file.
        edits: List of Edit operations to apply.

    Returns:
        PatchResult with modified content and per-edit outcomes.
    """
    if not edits:
        return PatchResult(
            content=content,
            results=[],
            applied_count=0,
            rejected_count=0,
        )

    # Check for overlaps before doing anything.
    overlap_err = _check_overlap(edits)
    if overlap_err:
        return PatchResult(
            content=content,
            results=[
                ApplyResult(edit=e, applied=False, rejection_reason=overlap_err)
                for e in edits
            ],
            applied_count=0,
            rejected_count=len(edits),
        )

    # Validate and apply in reverse-offset order.
    # Sort by start descending so we modify the end of the string first.
    indexed = list(enumerate(edits))
    indexed.sort(key=lambda pair: pair[1].start, reverse=True)

    result_map: dict[int, ApplyResult] = {}
    mutable_content = content

    for orig_idx, edit in indexed:
        err = _validate_edit(edit, mutable_content)
        if err:
            result_map[orig_idx] = ApplyResult(edit=edit, applied=False, rejection_reason=err)
            continue

        # Apply the edit.
        mutable_content = mutable_content[:edit.start] + edit.replacement + mutable_content[edit.end:]
        result_map[orig_idx] = ApplyResult(edit=edit, applied=True)

    # Restore original order.
    results = [result_map[i] for i in range(len(edits))]
    applied = sum(1 for r in results if r.applied)

    return PatchResult(
        content=mutable_content,
        results=results,
        applied_count=applied,
        rejected_count=len(edits) - applied,
    )


def line_col_to_offset(content: str, line: int, col: int = 0) -> int:
    """Convert 1-indexed line number + 0-indexed column to a Python string offset.

    This is a convenience for converting critic line ranges to offsets.
    Line 1 = first line. Column 0 = first character of the line.
    """
    lines = content.split("\n")
    if line < 1 or line > len(lines):
        raise ValueError(f"Line {line} out of range (1-{len(lines)})")
    offset = sum(len(lines[i]) + 1 for i in range(line - 1))  # +1 for \n
    line_text = lines[line - 1]
    if col > len(line_text):
        raise ValueError(f"Column {col} out of range for line {line} (length {len(line_text)})")
    return offset + col


def find_quoted_span(content: str, quoted_text: str, after_offset: int = 0) -> tuple[int, int] | None:
    """Find the first occurrence of quoted_text in content after after_offset.

    Returns (start, end) or None if not found. This is a fallback for when
    line ranges are unreliable — the quoted text from the critic finding is
    often a more reliable locator than the line number.
    """
    idx = content.find(quoted_text, after_offset)
    if idx == -1:
        return None
    return (idx, idx + len(quoted_text))
