"""
finding_parser.py — Extract and classify findings from critic output files.

Handles every format found on disk across all critic types. Standalone module.
No orchestrator imports, no settings. Ports by copying.

Format catalogue (see docs/arrs/19 §Part 1):
  A. ### N. Line N–N — Type  +  **Text:** "..."
  B. ### N. "pattern" — SEVERITY  +  - **L119:** "..."
  C. ### N. Title — SEVERITY  +  | Lines | table rows |
  D. ### N. Title — SEVERITY  +  - **L7:** "..."  (inline list)
  E. > "quoted text"  +  > — line N  (voice/palette blockquotes)
  F. ### ⚠️ BLOCKING — "description" (line N)  +  > "quoted text"
  G. **[Line N]** [Type]  +  > quoted text  (prose_audit)
  H. **[Line N/A]** [Type]  +  > quoted text  (prose_audit, no line)
  I. **Location:** Line N  +  **Text:**  +  > quoted text  (ch13/14 show)
  J. ### N. Title  +  > "quoted text"  +  — Lines N–N  (palette)
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedFinding:
    """A finding extracted from a critic output file."""
    source_file: str
    chapter: Optional[int]
    critic_type: Optional[str]
    finding_num: int
    line_start: Optional[int]
    line_end: Optional[int]
    quoted_text: Optional[str]
    finding_type: Optional[str]
    severity: Optional[str]
    is_clean: bool
    format_tag: str  # Which format was matched (A–J)
    body_preview: str
    body_full: str   # Full finding body for decline detection
    section_id: str = ""  # Groups findings under the same ### header


# ── Regex patterns ──────────────────────────────────────────────────────────

# Format A: ### 1. Line 39–41 — Type (Severity)
_RE_A_HEADER = re.compile(
    r'^###?\s+(\d+)\.\s*Lines?\s+(\d+)(?:\s*[–\-]\s*(\d+))?\s*[—\-]\s*(.+)',
    re.MULTILINE
)
_RE_A_TEXT = re.compile(r'\*\*Text:\*\*\s*["\u201c](.+?)["\u201d]', re.DOTALL)
_RE_A_BLOCKQUOTE = re.compile(r'^>\s*["\u201c](.+?)["\u201d]', re.MULTILINE)

# Format B: ### 1. "There was" existential expletive — CRITICAL
_RE_B_HEADER = re.compile(
    r'^###?\s+(\d+)\.\s*["\u201c](.+?)["\u201d]\s*(.*?)\s*[—\-]\s*(.+)',
    re.MULTILINE
)
# Sub-items: - **L119:** "quoted text"  (colon inside bold)
_RE_B_SUB = re.compile(
    r'[-*]\s*\*\*L(\d+):?\*\*\s*:?\s*["\u201c](.+?)["\u201d]',
    re.MULTILINE
)

# Format C: table rows with | L29 | ... | or | Lines | ... |
_RE_C_TABLE = re.compile(
    r'\|\s*L(\d+)(?:\s*[–\-]\s*(\d+))?\s*\|(.+?)\|',
    re.MULTILINE
)

# Format D: inline list items with **L7:** "quoted text" (colon inside bold)
_RE_D_SUB = re.compile(
    r'[-*]\s*\*\*L(\d+)(?:\s*[–\-]\s*(\d+))?:?\*\*\s*:?\s*["\u201c](.+?)["\u201d]',
    re.MULTILINE
)

# Format E: > "quoted text"  +  > — line N  (voice/palette blockquotes)
_RE_E_QUOTE = re.compile(
    r'^>\s*["\u201c](.+?)["\u201d]\s*\n>\s*[—\-]\s*lines?\s+(\d+)(?:\s*[–\-]\s*(\d+))?',
    re.MULTILINE | re.DOTALL
)

# Format F: ### ⚠️ BLOCKING — "description" (line N)
_RE_F_HEADER = re.compile(
    r'^###?\s+[⚠️🔔]+\s*\w+\s*[—\-]\s*["\u201c](.+?)["\u201d]\s*\((?:line|lines?)\s+(\d+)(?:\s*[–\-]\s*(\d+))?\)',
    re.MULTILINE
)
_RE_F_QUOTE = re.compile(r'^>\s*["\u201c](.+?)["\u201d]', re.MULTILINE)

# Format G: **[Line N]** [Type]  +  > quoted text  (prose_audit)
_RE_G = re.compile(
    r'\*\*\[Line\s+(\d+)\]\*\*\s*\[(.+?)\]\s*\n\s*>\s*(.+)',
    re.MULTILINE
)

# Format H: **[Line N/A]** [Type]  (prose_audit, no line)
_RE_H = re.compile(
    r'\*\*\[Line\s+N/?A\]\*\*\s*\[(.+?)\]\s*\n\s*>\s*(.+)',
    re.MULTILINE
)

# Format I: **Location:** Line N  +  **Text:**  +  > quoted text  (ch13/14)
# Also handles **Text:** "inline quotes" (ch2, ch3, etc.)
# Also handles **Text:** `backtick quotes` (ch5, etc.)
_RE_I_LOC = re.compile(r'\*\*Location:\*\*\s*Lines?\s+(\d+)(?:\s*[–\-]\s*(\d+))?')
_RE_I_TEXT = re.compile(r'\*\*Text:\*\*\s*(?:\n?\s*>\s*|\s*["\u201c]|\s*`)(.+?)(?:["\u201d]`|\n)', re.MULTILINE)

# Format J: > "quoted text"  +  — Lines N–N  (palette)
_RE_J_QUOTE = re.compile(
    r'^>\s*["\u201c](.+?)["\u201d]\s*\n[—\-]\s*Lines?\s+(\d+)(?:\s*[–\-]\s*(\d+))?',
    re.MULTILINE | re.DOTALL
)

# Format K: > "quoted text"  +  **Location:** Line N  (ch7 show, naturalism)
_RE_K_QUOTE = re.compile(
    r'^>\s*["\u201c](.+?)["\u201d]',
    re.MULTILINE
)
_RE_K_LOC = re.compile(r'\*\*Location:\*\*\s*Lines?\s+(\d+)(?:\s*[–\-]\s*(\d+))?')

# Finding header for splitting
_FINDING_HEADER_RE = re.compile(r'^(###?\s+\d+\.|###?\s+[⚠️🔔])', re.MULTILINE)

# Chapter hash extraction
_HASH_RE = re.compile(r'chapter_hash[:\s]*([a-f0-9]{64})', re.IGNORECASE)


def _get_chapter_num(filename: str) -> Optional[int]:
    m = re.match(r'(\d+)_', filename)
    if m:
        return int(m.group(1))
    m = re.match(r'chapter_(\d+)_', filename)
    if m:
        return int(m.group(1))
    return None


def _get_critic_type(filename: str) -> Optional[str]:
    for ct in ['show_dont_tell', 'voice', 'palette', 'continuity', 'naturalism', 'prose_audit']:
        if ct in filename:
            return ct
    return None


def _severity_from_type(type_str: str) -> Optional[str]:
    if not type_str:
        return None
    t = type_str.lower()
    if 'critical' in t or 'blocking' in t or 'tier 1' in t or 'dominant' in t:
        return 'critical'
    if 'moderate' in t or 'high' in t or 'tier 2' in t:
        return 'moderate'
    if 'minor' in t or 'borderline' in t or 'flag' in t:
        return 'minor'
    return None


def extract_findings(filepath: str, chapter_content: str = "") -> list[ParsedFinding]:
    """Extract all findings from a critic output file.

    Args:
        filepath: Path to the critic output file.
        chapter_content: Current chapter text for quote verification (optional).

    Returns:
        List of ParsedFinding objects.
    """
    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            content = f.read()
    except Exception:
        return []

    filename = os.path.basename(filepath)
    chapter_num = _get_chapter_num(filename)
    critic_type = _get_critic_type(filename)

    findings = []
    finding_num = 0

    # ── Format A: ### N. Line N–N — Type  +  **Text:** "..." ──────────
    for m in _RE_A_HEADER.finditer(content):
        finding_num += 1
        num = int(m.group(1))
        line_start = int(m.group(2))
        line_end = int(m.group(3)) if m.group(3) else line_start
        ftype = m.group(4).strip()

        # Find the **Text:** in the body after this header
        body_start = m.end()
        next_header = _FINDING_HEADER_RE.search(content, body_start)
        body = content[body_start:next_header.start()] if next_header else content[body_start:]
        text_m = _RE_A_TEXT.search(body)
        if text_m:
            quoted = text_m.group(1)
        else:
            # Fallback: check for blockquote text (> "quoted text")
            bq_m = _RE_A_BLOCKQUOTE.search(body)
            quoted = bq_m.group(1) if bq_m else None

        findings.append(ParsedFinding(
            source_file=filename, chapter=chapter_num, critic_type=critic_type,
            finding_num=num, line_start=line_start, line_end=line_end,
            quoted_text=quoted, finding_type=ftype, severity=_severity_from_type(ftype),
            is_clean=False, format_tag='A', body_preview=body[:300].strip(),
            body_full=body.strip(),
        ))

    # ── Format B: ### N. "pattern" — SEVERITY  +  - **L119:** "..." ───
    for m in _RE_B_HEADER.finditer(content):
        finding_num += 1
        num = int(m.group(1))
        pattern = m.group(2).strip()
        description = m.group(3).strip() if m.group(3) else ""
        severity_str = m.group(4).strip()
        full_type = f"{pattern} {description}".strip() if description else pattern

        body_start = m.end()
        next_header = _FINDING_HEADER_RE.search(content, body_start)
        body = content[body_start:next_header.start()] if next_header else content[body_start:]

        # Extract sub-items
        subs = list(_RE_B_SUB.finditer(body))
        if subs:
            for sub in subs:
                line_start = int(sub.group(1))
                quoted = sub.group(2)
                findings.append(ParsedFinding(
                    source_file=filename, chapter=chapter_num, critic_type=critic_type,
                    finding_num=num, line_start=line_start, line_end=line_start,
                    quoted_text=quoted, finding_type=full_type,
                    severity=_severity_from_type(severity_str),
                    is_clean=False, format_tag='B', body_preview=body[:300].strip(),
                    body_full=body.strip(),
                ))
        else:
            # Pattern finding with no sub-items — structural
            findings.append(ParsedFinding(
                source_file=filename, chapter=chapter_num, critic_type=critic_type,
                finding_num=num, line_start=None, line_end=None,
                quoted_text=None, finding_type=full_type,
                severity=_severity_from_type(severity_str),
                is_clean=False, format_tag='B', body_preview=body[:300].strip(),
                body_full=body.strip(),
            ))

    # ── Format C: table rows | L29 | ... | ─────────────────────────────
    # These can be standalone or embedded inside format B findings.
    # Always scan the full content for table rows with line references.
    for m in _RE_C_TABLE.finditer(content):
        line_start = int(m.group(1))
        line_end = int(m.group(2)) if m.group(2) else line_start
        desc = m.group(3).strip()
        # Skip if this line range was already captured by format A or B
        already = any(f.line_start == line_start and f.format_tag in ('A', 'B')
                      for f in findings)
        if not already:
            finding_num += 1
            # Find the enclosing section body for decline detection.
            section_start = content.rfind('\n###', 0, m.start())
            if section_start == -1:
                section_start = 0
            else:
                section_start += 1
            section_end = _FINDING_HEADER_RE.search(content, m.end())
            section_body = content[section_start:section_end.start()] if section_end else content[section_start:]
            findings.append(ParsedFinding(
                source_file=filename, chapter=chapter_num, critic_type=critic_type,
                finding_num=finding_num, line_start=line_start, line_end=line_end,
                quoted_text=None, finding_type=desc,
                severity=None, is_clean=False, format_tag='C',
                body_preview=desc[:300],
                body_full=section_body.strip(),
            ))

    # ── Format D: inline list - **L7:** "quoted text" ─────────────────
    # Capture inline list items from findings not already captured by format B.
    # Track which finding numbers were already captured by format B sub-items.
    b_captured_nums = {f.finding_num for f in findings if f.format_tag == 'B'}
    for m in _RE_D_SUB.finditer(content):
        # Check if this line reference overlaps with an already-captured finding
        line_start = int(m.group(1))
        already = any(f.line_start == line_start and f.finding_num in b_captured_nums
                      for f in findings)
        if not already:
            finding_num += 1
            line_end = int(m.group(2)) if m.group(2) else line_start
            quoted = m.group(3)
            # Find the enclosing section body for decline detection.
            # The inline list item is inside a section delimited by ### headers.
            # Walk backward from the match to find the enclosing header.
            section_start = content.rfind('\n###', 0, m.start())
            if section_start == -1:
                section_start = 0
            else:
                section_start += 1  # skip the newline
            section_end = _FINDING_HEADER_RE.search(content, m.end())
            section_body = content[section_start:section_end.start()] if section_end else content[section_start:]

            # Derive section_id from the enclosing ### header text.
            # This groups all sub-items under the same header.
            header_line = content[section_start:section_start + 200].split('\n')[0]
            section_id = f"{filename}:{header_line.strip()[:80]}"

            findings.append(ParsedFinding(
                source_file=filename, chapter=chapter_num, critic_type=critic_type,
                finding_num=finding_num, line_start=line_start, line_end=line_end,
                quoted_text=quoted, finding_type=None,
                severity=None, is_clean=False, format_tag='D',
                body_preview=quoted[:300],
                body_full=section_body.strip(),
                section_id=section_id,
            ))

    # ── Format E: > "quoted text" + > — line N  (voice/palette) ───────
    for m in _RE_E_QUOTE.finditer(content):
        finding_num += 1
        quoted = m.group(1)
        line_start = int(m.group(2))
        line_end = int(m.group(3)) if m.group(3) else line_start
        findings.append(ParsedFinding(
            source_file=filename, chapter=chapter_num, critic_type=critic_type,
            finding_num=finding_num, line_start=line_start, line_end=line_end,
            quoted_text=quoted, finding_type=None,
            severity=None, is_clean=False, format_tag='E',
            body_preview=quoted[:300],
            body_full=quoted,
        ))

    # ── Format F: ### ⚠️ BLOCKING — "description" (line N) ───────────
    for m in _RE_F_HEADER.finditer(content):
        finding_num += 1
        desc = m.group(1)
        line_start = int(m.group(2))
        line_end = int(m.group(3)) if m.group(3) else line_start

        body_start = m.end()
        next_header = _FINDING_HEADER_RE.search(content, body_start)
        body = content[body_start:next_header.start()] if next_header else content[body_start:]
        quote_m = _RE_F_QUOTE.search(body)
        quoted = quote_m.group(1) if quote_m else None

        findings.append(ParsedFinding(
            source_file=filename, chapter=chapter_num, critic_type=critic_type,
            finding_num=finding_num, line_start=line_start, line_end=line_end,
            quoted_text=quoted, finding_type=desc,
            severity='critical', is_clean=False, format_tag='F',
            body_preview=body[:300].strip(),
            body_full=body.strip(),
        ))

    # ── Format G: **[Line N]** [Type] + > quoted text  (prose_audit) ──
    for m in _RE_G.finditer(content):
        finding_num += 1
        line_start = int(m.group(1))
        ftype = m.group(2).strip()
        quoted = m.group(3).strip()
        findings.append(ParsedFinding(
            source_file=filename, chapter=chapter_num, critic_type=critic_type,
            finding_num=finding_num, line_start=line_start, line_end=line_start,
            quoted_text=quoted, finding_type=ftype,
            severity=_severity_from_type(ftype),
            is_clean=False, format_tag='G',
            body_preview=quoted[:300],
            body_full=quoted,
        ))

    # ── Format H: **[Line N/A]** [Type]  (prose_audit, no line) ───────
    for m in _RE_H.finditer(content):
        finding_num += 1
        ftype = m.group(1).strip()
        quoted = m.group(2).strip()
        findings.append(ParsedFinding(
            source_file=filename, chapter=chapter_num, critic_type=critic_type,
            finding_num=finding_num, line_start=None, line_end=None,
            quoted_text=quoted, finding_type=ftype,
            severity=_severity_from_type(ftype),
            is_clean=False, format_tag='H',
            body_preview=quoted[:300],
            body_full=quoted,
        ))

    # ── Format K: > "quoted text" + **Location:** Line N  (ch7 show, nat)
    # Blockquote comes BEFORE the location. Scan for quote-then-location pairs.
    # Runs BEFORE format I so that quotes take priority over bare locations.
    if not any(f.format_tag in ('A', 'E', 'J') for f in findings):
        k_quotes = list(_RE_K_QUOTE.finditer(content))
        k_locs = list(_RE_K_LOC.finditer(content))
        for i, qm in enumerate(k_quotes):
            q_end = qm.end()
            nearest_loc = None
            for loc_m in k_locs:
                if loc_m.start() > q_end:
                    nearest_loc = loc_m
                    break
            if nearest_loc:
                finding_num += 1
                quoted = qm.group(1)
                line_start = int(nearest_loc.group(1))
                line_end = int(nearest_loc.group(2)) if nearest_loc.group(2) else line_start
                already = any(f.line_start == line_start and f.quoted_text == quoted for f in findings)
                if not already:
                    findings.append(ParsedFinding(
                        source_file=filename, chapter=chapter_num, critic_type=critic_type,
                        finding_num=finding_num, line_start=line_start, line_end=line_end,
                        quoted_text=quoted, finding_type=None,
                        severity=None, is_clean=False, format_tag='K',
                        body_preview=quoted[:300],
                        body_full=quoted,
                    ))

    # ── Format I: **Location:** Line N  +  **Text:** > quoted  ────────
    # Only for findings not already captured by format A or K
    if not any(f.format_tag in ('A', 'K') for f in findings):
        loc_matches = list(_RE_I_LOC.finditer(content))
        text_matches = list(_RE_I_TEXT.finditer(content))
        for i, loc_m in enumerate(loc_matches):
            line_start = int(loc_m.group(1))
            already = any(f.line_start == line_start and f.quoted_text for f in findings)
            if already:
                continue
            finding_num += 1
            line_end = int(loc_m.group(2)) if loc_m.group(2) else line_start
            quoted = text_matches[i].group(1) if i < len(text_matches) else None
            findings.append(ParsedFinding(
                source_file=filename, chapter=chapter_num, critic_type=critic_type,
                finding_num=finding_num, line_start=line_start, line_end=line_end,
                quoted_text=quoted, finding_type=None,
                severity=None, is_clean=False, format_tag='I',
                body_preview=(quoted or "")[:300],
                body_full=quoted or "",
            ))

    # ── Format J: > "quoted text" + — Lines N–N  (palette) ────────────
    # Only if format E didn't already capture these
    if not any(f.format_tag == 'E' for f in findings):
        for m in _RE_J_QUOTE.finditer(content):
            finding_num += 1
            quoted = m.group(1)
            line_start = int(m.group(2))
            line_end = int(m.group(3)) if m.group(3) else line_start
            findings.append(ParsedFinding(
                source_file=filename, chapter=chapter_num, critic_type=critic_type,
                finding_num=finding_num, line_start=line_start, line_end=line_end,
                quoted_text=quoted, finding_type=None,
                severity=None, is_clean=False, format_tag='J',
                body_preview=quoted[:300],
                body_full=quoted,
            ))

    # ── Clean passage detection ────────────────────────────────────────
    for f in findings:
        if f.finding_type and any(kw in f.finding_type.lower() for kw in
                                   ['pass', 'clean', 'no violation', 'no issue', 'effective']):
            f.is_clean = True

    # ── Quote verification against chapter ─────────────────────────────
    if chapter_content:
        for f in findings:
            if f.quoted_text:
                idx = chapter_content.find(f.quoted_text)
                if idx == -1:
                    # Try truncated quote (critics sometimes truncate with ...)
                    truncated = f.quoted_text.rstrip('.').rstrip()
                    if len(truncated) > 20:
                        idx = chapter_content.find(truncated[:50])
                if idx >= 0:
                    f._verified_offset = (idx, idx + len(f.quoted_text))
                else:
                    f._verified_offset = None  # stale

    return findings


def extract_hash(filepath: str) -> Optional[str]:
    """Extract the chapter_hash from a critic output file."""
    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            content = f.read()
        m = _HASH_RE.search(content)
        return m.group(1) if m else None
    except Exception:
        return None
