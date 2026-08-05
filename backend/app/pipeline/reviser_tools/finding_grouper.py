"""
finding_grouper.py — Group findings by section for context-preserving dispatch.

A finding group is the findings under one section of one critic's output,
together with the section body. This preserves the keep/fix partitions,
exemplar dependencies, and ordering constraints observed in §22 Part 4.

Standalone module. No orchestrator imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FindingGroup:
    """A group of findings from one section of one critic's output."""
    source_file: str
    chapter: Optional[int]
    critic_type: Optional[str]
    section_header: str         # The ### or ## heading text
    section_body: str           # Full section body text
    findings: list[dict]        # List of finding dicts
    dominant_class: str         # "surgical_instance", "surgical_pattern",
                                # "structural", "mixed", "unclassifiable"
    has_do_not_edit: bool        # True if any finding is do-not-edit
    do_not_edit_ids: list[str]  # IDs of do-not-edit findings (for context)


def _derive_finding_id(finding: dict) -> str:
    """Derive a stable finding ID from parsed fields."""
    import hashlib
    ch = finding.get("chapter") or 0
    critic = (finding.get("critic_type") or "unknown").replace("_", "-")
    quote = finding.get("quoted_text")
    if quote and len(quote) > 10:
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


def _determine_dominant_class(findings: list[dict]) -> str:
    """Determine the dominant quadrant class for a group of findings.

    Rules:
    - If all findings are the same class, that's the dominant class.
    - If mixed, return "mixed" — the router must handle this.
    - do_not_edit findings are counted but don't determine the dominant class.
    """
    classes = set()
    for f in findings:
        c = f.get("_classification")
        if c is None:
            continue
        if f.get("_do_not_edit"):
            continue  # Don't count do-not-edit findings
        span = c.span_class
        scope = c.scope_class
        if span == "surgical" and scope == "instance":
            classes.add("surgical_instance")
        elif span == "surgical" and scope == "pattern":
            classes.add("surgical_pattern")
        elif span == "structural":
            classes.add("structural")
        elif span == "unclassifiable":
            classes.add("unclassifiable")
        else:
            classes.add("unclassified")

    if not classes:
        return "unclassifiable"
    if len(classes) == 1:
        return classes.pop()
    return "mixed"


def group_findings(findings: list[dict]) -> list[FindingGroup]:
    """Group findings by source file and section.

    A section is identified by the finding's section_id field (set by the
    parser). Format D findings share a section_id derived from their enclosing
    ### header. Format B findings share a section_id derived from their parent
    finding number. All other formats get a unique section_id per finding.
    """
    sections: dict[str, list[dict]] = {}

    for f in findings:
        src = f.get("source_file", "")
        fmt = f.get("format_tag", "")
        num = f.get("num", 0)

        # Use section_id if available (format D), otherwise derive from format
        sid = f.get("section_id", "")
        if not sid:
            if fmt in ("B",):
                # Format B: sub-items share a parent finding num
                sid = f"{src}:B:{num}"
            else:
                # Each finding is its own section
                sid = f"{src}:{f.get('finding_num', num)}"

        if sid not in sections:
            sections[sid] = []
        sections[sid].append(f)

    # Build groups
    groups = []
    for sid, group_findings_list in sections.items():
        # Determine section header and body from the first finding
        first = group_findings_list[0]
        body_full = first.get("body_full") or first.get("body_preview") or ""
        src = first.get("source_file", "")

        # Determine do-not-edit status
        dne_ids = []
        for f in group_findings_list:
            if f.get("_do_not_edit"):
                dne_ids.append(_derive_finding_id(f))

        # Determine dominant class
        dominant = _determine_dominant_class(group_findings_list)

        groups.append(FindingGroup(
            source_file=src,
            chapter=first.get("chapter"),
            critic_type=first.get("critic_type"),
            section_header=sid,
            section_body=body_full,
            findings=group_findings_list,
            dominant_class=dominant,
            has_do_not_edit=bool(dne_ids),
            do_not_edit_ids=dne_ids,
        ))

    return groups
