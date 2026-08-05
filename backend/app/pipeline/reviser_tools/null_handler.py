"""
null_handler.py — A handler that produces no edits.

This is the "do nothing" handler. It proves the pipe works: every finding
that routes to it receives context and returns cleanly. When a real prompt
goes in, a failure is the prompt's.

No LLM calls. No orchestrator imports.
"""

from __future__ import annotations

from app.pipeline.reviser_tools.reviser_router import FindingHandler, RoutedFinding, HandlerResult


class NullHandler:
    """A handler that produces no edits. Every finding returns 'attempted'
    with an empty edit list."""

    async def handle(self, finding: RoutedFinding) -> HandlerResult:
        return HandlerResult(
            proposed_edits=[],
            disposition="attempted",
            reason="null handler — no edits produced",
        )
