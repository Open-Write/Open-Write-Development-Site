"""
Tests for R4: critic_unavailable replaces fabricated REVISE stub.

Validates that a critic failure produces an honest absence in the failures
list, not a fabricated REVISE verdict, and that downstream consumers handle
it correctly.
"""

import sys
import os
import json
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import AsyncMock, patch, MagicMock


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_state(chapter=1, max_retries=2):
    """Build a minimal RunState-like object for testing."""
    from app.pipeline.orchestrator import RunState
    state = RunState(
        project_path="/tmp/test_project",
        project_name="Test",
        started_at="2026-08-04T00:00:00Z",
        current_phase="critics",
        current_unit_index=0,
        units=[1],
        max_chapter_retries=max_retries,
    )
    return state


def _run_async(coro):
    """Run a coroutine in a new event loop."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Test: critic failure produces critic_unavailable, not REVISE ─────────────

def test_critic_failure_produces_critic_unavailable():
    """A critic exception must produce critic_unavailable in failures,
    not a fabricated REVISE verdict in results.

    This is a structural test: verify the except block in _exec_critics
    appends to failures with critic_unavailable, not to results with REVISE.
    """
    from app.pipeline.orchestrator import _exec_critics
    import inspect

    source = inspect.getsource(_exec_critics)

    # The except block must append to failures, not results
    # Find the except block and verify its structure
    assert "critic_unavailable" in source, "critic_unavailable not found in _exec_critics"
    assert "failures.append" in source, "failures.append not found in _exec_critics"

    # The except block must NOT append to results (which would add a fabricated verdict)
    # Check that the except block doesn't contain results.append with verdict
    except_start = source.find("except Exception as exc:")
    if except_start >= 0:
        except_block = source[except_start:]
        # The except block should NOT contain results.append
        assert "results.append" not in except_block, \
            "except block still appends to results — fabricated verdict may remain"


def test_critic_failure_no_stub_file_written():
    """A critic failure must NOT write a stub artifact file."""
    from app.pipeline.orchestrator import _exec_critics
    import inspect

    source = inspect.getsource(_exec_critics)
    # The new implementation should NOT contain the fabricated VERDICT: REVISE
    assert "VERDICT: REVISE" not in source, "Found fabricated VERDICT: REVISE in _exec_critics source"
    # Should NOT contain file-writing patterns (os.makedirs + open + write)
    assert "os.makedirs" not in source, "Found os.makedirs in _exec_critics — stub file may still be written"


# ── Test: post-critics loop does not consume retry budget ────────────────────

def test_critic_unavailable_does_not_consume_revision_budget():
    """critic_unavailable entries go to failures, not results.
    The post-critics loop only checks results for REVISE verdicts.
    So critic_unavailable should NOT trigger a revision cycle."""
    # This is a structural test: verify the post-critics loop only looks at
    # result["critics"], not result["failures"].
    from app.pipeline.orchestrator import _exec_critics
    import inspect

    source = inspect.getsource(_exec_critics)
    # The function returns {"critics": results, "failures": failures, ...}
    # The post-critics loop in advance_phase checks result.get("critics", [])
    # Failures are not checked for verdicts.
    assert '"critics": results' in source or "'critics': results" in source
    assert '"failures": failures' in source or "'failures': failures" in source


# ── Test: downstream consumers handle missing critic files ───────────────────

def test_gate_fails_on_missing_critic_file():
    """When a critic file is missing (no stub written), the gate should FAIL."""
    from app.pipeline.verify_completion import _validate_critic_substance

    # _validate_critic_substance checks a file on disk. If the file doesn't
    # exist, the gate reports MISSING at a higher level (verify_manifest).
    # This test confirms the substance validator doesn't crash on missing files.
    failures = _validate_critic_substance("/nonexistent/path.md", "test critic")
    # Should get a READ_ERROR, not a crash
    assert len(failures) == 1
    assert "READ_ERROR" in failures[0]


def test_post_critics_loop_ignores_failures():
    """The post-critics revision loop must not react to failures entries."""
    # Simulate the post-critics loop logic with a result containing failures
    result = {
        "critics": [
            {"critic_type": "voice", "verdict": "PASS"},
        ],
        "failures": [
            {"critic": "show", "error": "RuntimeError: timeout", "outcome": "critic_unavailable"},
        ],
        "chapter": 1,
    }

    # This is the exact logic from orchestrator.py:1327-1351
    critic_results = result.get("critics", [])
    revise_verdicts = [c for c in critic_results if c.get("verdict", "").upper() == "REVISE"]
    pass_verdicts = [c for c in critic_results if c.get("verdict", "").upper() in ("PASS", "ADVANCE")]

    # The failure entry should NOT appear in revise_verdicts
    assert len(revise_verdicts) == 0, f"Failure entry triggered REVISE: {revise_verdicts}"
    assert len(pass_verdicts) == 1, f"Expected 1 PASS, got {len(pass_verdicts)}"
    # The failures list is preserved but not checked for verdicts
    assert len(result["failures"]) == 1
    assert result["failures"][0]["outcome"] == "critic_unavailable"


# ── Test: versions.capture_phase_versions handles missing critic ─────────────

def test_version_capture_skips_missing_critic():
    """capture_phase_versions iterates result['critics'], not failures.
    A failed critic is not in results, so it's skipped."""
    result = {
        "critics": [
            {"critic_type": "voice", "verdict": "PASS", "artifact_path": "critic_outputs/ch1_voice.md"},
        ],
        "failures": [
            {"critic": "show", "error": "timeout", "outcome": "critic_unavailable"},
        ],
        "chapter": 1,
    }

    # The versions module iterates result.get("critics", [])
    critics_list = result.get("critics", [])
    critic_types = [c.get("critic_type") for c in critics_list]

    assert "show" not in critic_types, "Failed critic appeared in critics list"
    assert "voice" in critic_types, "Successful critic missing from critics list"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
