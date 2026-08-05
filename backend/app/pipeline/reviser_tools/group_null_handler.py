"""
group_null_handler.py — A group-based handler that produces no edits.

Proves the group pipe works. Every finding group receives context and returns
cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.pipeline.reviser_tools.finding_grouper import FindingGroup


@dataclass(frozen=True)
class GroupHandlerResult:
    """Output from a group-based handler."""
    proposed_edits: list[dict]
    disposition: str            # "attempted", "declined", "unable"
    reason: str
    findings_attempted: int
    findings_skipped: int       # do-not-edit findings in the group


class GroupNullHandler:
    """A group handler that produces no edits."""

    async def handle(self, group: FindingGroup, chapter_text: str) -> GroupHandlerResult:
        # Count do-not-edit findings (they stay as context, not dispatched)
        dne_count = len(group.do_not_edit_ids)
        attempted = len(group.findings) - dne_count

        return GroupHandlerResult(
            proposed_edits=[],
            disposition="attempted",
            reason="null group handler — no edits produced",
            findings_attempted=attempted,
            findings_skipped=dne_count,
        )
