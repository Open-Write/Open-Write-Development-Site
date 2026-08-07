"""Tests for the help bot router.

Verifies:
- The help router is properly registered
- The system prompt contains required security boundaries
- The system prompt correctly declines pipeline control and craft feedback
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestHelpRouterStructure:
    """Verify the help router is properly set up."""

    def test_router_imports(self):
        from app.routers.help_router import router
        assert router.prefix == "/api/help"

    def test_chat_route_exists(self):
        from app.routers.help_router import router
        paths = [str(route.path) for route in router.routes]
        assert any("chat" in p for p in paths), f"No chat route found in {paths}"

    def test_chat_requires_auth(self):
        import inspect
        from app.routers.help_router import help_chat
        sig = inspect.signature(help_chat)
        params = list(sig.parameters.values())
        assert any(p.name == "current" for p in params), "Missing 'current' auth param"


class TestHelpSystemPrompt:
    """Verify the system prompt contains required guidance."""

    def test_contains_security_boundaries(self):
        from app.routers.help_router import HELP_SYSTEM_PROMPT
        assert "SECURITY BOUNDARIES" in HELP_SYSTEM_PROMPT
        assert "help assistant ONLY" in HELP_SYSTEM_PROMPT

    def test_declines_pipeline_control(self):
        from app.routers.help_router import HELP_SYSTEM_PROMPT
        assert "pipeline" in HELP_SYSTEM_PROMPT.lower()
        assert "Pipeline Chat" in HELP_SYSTEM_PROMPT

    def test_declines_craft_feedback(self):
        from app.routers.help_router import HELP_SYSTEM_PROMPT
        assert "Writing Companion" in HELP_SYSTEM_PROMPT
        assert "Editorial Review" in HELP_SYSTEM_PROMPT

    def test_describes_pipeline_phases(self):
        from app.routers.help_router import HELP_SYSTEM_PROMPT
        for phase in ["Bible", "Voice", "Editorial", "Writing", "Assembly", "Adversarial", "Finalize"]:
            assert phase.lower() in HELP_SYSTEM_PROMPT.lower(), f"Missing pipeline phase: {phase}"

    def test_describes_output_library(self):
        from app.routers.help_router import HELP_SYSTEM_PROMPT
        assert "Output Library" in HELP_SYSTEM_PROMPT or "Output" in HELP_SYSTEM_PROMPT

    def test_describes_version_history(self):
        from app.routers.help_router import HELP_SYSTEM_PROMPT
        assert "version" in HELP_SYSTEM_PROMPT.lower()
        assert "restore" in HELP_SYSTEM_PROMPT.lower()
        assert "diff" in HELP_SYSTEM_PROMPT.lower()
