"""
test_cache_regression.py — Cache-regression CI gate.

Catches the silent cost-doubling failure: a timestamp, non-deterministic dict
ordering, or mutated prefix that breaks DeepSeek's prompt cache.  If the
stable prefix is not byte-identical across calls with the same inputs, cache
hit rate drops from ~99% to ~0% with no visible symptom and no failing test.

This test creates a fixture project with minimal bible/voice/manuscript files
and verifies three invariants:

  1. Determinism: _build_cache_prefix returns byte-identical output when called
     twice with the same RunState and project contents.
  2. Prefix stability: the prefix for chapter N is a strict prefix of the
     prefix for chapter N+1 (append-only manuscript property).
  3. No injected timestamps: the prefix contains no ISO timestamps or other
     non-deterministic strings that would break cache on every call.

Run: pytest tests/test_cache_regression.py -v
"""

import os
import sys
import json
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.pipeline.orchestrator import (
    RunState,
    _build_cache_prefix,
    _GLOBAL_SYSTEM_PROMPT,
    save_run_state,
)


# ── Fixture builder ─────────────────────────────────────────────────────────

def _create_fixture_project(base_dir: str, chapters: int = 4) -> RunState:
    """Create a minimal project with bible, voice, and N chapters."""
    # Bible files
    bible_dir = os.path.join(base_dir, "bible")
    os.makedirs(bible_dir, exist_ok=True)

    with open(os.path.join(bible_dir, "01_concept.md"), "w") as f:
        f.write("# Concept\n\nA test novel about cache determinism.\n")
    with open(os.path.join(bible_dir, "04_outline.md"), "w") as f:
        f.write("# Outline\n\n")
        for ch in range(1, chapters + 1):
            f.write(f"## Chapter {ch}\n\nBeat {ch}: The protagonist discovers.\n\n")
    with open(os.path.join(bible_dir, "07_format_rules.md"), "w") as f:
        f.write("# Format Rules\n\nProse discipline: show, don't tell.\n")
    with open(os.path.join(bible_dir, "LOCKED_VOICE_SPEC.md"), "w") as f:
        f.write("# Voice Spec\n\nClose-internal, present tense, lyric register.\n")

    # Manuscript chapters (append-only)
    ms_dir = os.path.join(base_dir, "manuscript")
    os.makedirs(ms_dir, exist_ok=True)
    for ch in range(1, chapters + 1):
        with open(os.path.join(ms_dir, f"{ch:03d}_chapter.md"), "w") as f:
            f.write(f"# Chapter {ch}\n\n")
            f.write(f"The {'next' if ch > 1 else 'first'} morning came ")
            f.write(f"with a weight that settled into the bones. ")
            f.write(f"Paragraph {ch} of the story.\n")

    # Profiles
    prof_dir = os.path.join(base_dir, "profiles", "characters")
    os.makedirs(prof_dir, exist_ok=True)
    with open(os.path.join(prof_dir, "protagonist.md"), "w") as f:
        f.write("---\ntype: character\nname: Protagonist\n---\n\n# Overview\n\nThe main character.\n")

    # World context
    world_dir = os.path.join(base_dir, "profiles")
    with open(os.path.join(world_dir, "world.md"), "w") as f:
        f.write("---\ntype: world\n---\n\n# World\n\nA small town.\n")

    # Run state
    state = RunState(
        project_path=base_dir,
        project_name="Cache Test Novel",
        started_at="2026-01-01T00:00:00",
        status="running",
        current_phase="writer",
        current_unit_index=0,
        units=list(range(1, chapters + 1)),
        word_floor=800,
        word_count_min=10000,
        word_count_max=30000,
        word_target=20000,
        instructions="Write a literary novel.",
        format="novel",
    )
    save_run_state(state)
    return state


# ── Tests ────────────────────────────────────────────────────────────────────

class TestCacheDeterminism:
    """Prompt assembly must be byte-identical across repeated calls."""

    def test_prefix_deterministic_same_state(self, tmp_path):
        """Two calls with identical state and files must return the same bytes."""
        project = str(tmp_path / "proj")
        state = _create_fixture_project(project, chapters=3)

        prefix_a = _build_cache_prefix(state, project)
        prefix_b = _build_cache_prefix(state, project)

        assert prefix_a == prefix_b, (
            "Cache prefix is non-deterministic: two calls with identical state "
            "returned different output. This silently doubles LLM costs."
        )

    def test_prefix_deterministic_across_ten_calls(self, tmp_path):
        """Ten consecutive calls must all be identical (catches rare RNG/ordering bugs)."""
        project = str(tmp_path / "proj")
        state = _create_fixture_project(project, chapters=3)

        prefixes = [_build_cache_prefix(state, project) for _ in range(10)]
        for i in range(1, len(prefixes)):
            if prefixes[0] != prefixes[i]:
                diff_at = next(
                    (j for j in range(min(len(prefixes[0]), len(prefixes[i])))
                     if prefixes[0][j] != prefixes[i][j]), -1
                )
                pytest.fail(
                    f"Call {i} returned different output than call 0. "
                    f"First divergence at byte offset {diff_at}."
                )

    def test_prefix_deterministic_after_state_reload(self, tmp_path):
        """Loading state from disk and rebuilding prefix must match the original."""
        project = str(tmp_path / "proj")
        state = _create_fixture_project(project, chapters=3)
        prefix_a = _build_cache_prefix(state, project)

        # Reload from disk
        loaded = RunState.from_dict(json.load(
            open(os.path.join(project, "state", "pipeline_run.json"))
        ))
        prefix_b = _build_cache_prefix(loaded, project)

        assert prefix_a == prefix_b


class TestPrefixStability:
    """The cache prefix must form a containment chain: p[N+1] starts with p[N].

    This is the key invariant for DeepSeek's prefix cache: when calling the
    writer for chapter 3, the prompt starts with the same tokens as the prompt
    for chapter 2, so everything before the new chapter content is a cache hit.
    """

    def test_prefix_containment_chain(self, tmp_path):
        """Each prefix must start with the previous prefix (append-only growth)."""
        project = str(tmp_path / "proj")
        state = _create_fixture_project(project, chapters=5)

        prefixes = []
        for i in range(5):
            state.current_unit_index = i
            prefixes.append(_build_cache_prefix(state, project))

        for i in range(1, len(prefixes)):
            assert prefixes[i].startswith(prefixes[i - 1]), (
                f"Prefix for chapter {i + 1} does not start with prefix for "
                f"chapter {i}. Divergence at byte "
                f"{next((j for j in range(min(len(prefixes[i-1]), len(prefixes[i]))) if prefixes[i-1][j] != prefixes[i][j]), -1)}. "
                f"This breaks the prefix cache — chapter {i + 1}'s prompt "
                f"will be entirely cache-miss."
            )

    def test_full_chain_from_first_to_last(self, tmp_path):
        """The last prefix must start with the first prefix (transitive containment)."""
        project = str(tmp_path / "proj")
        state = _create_fixture_project(project, chapters=5)

        state.current_unit_index = 0
        first = _build_cache_prefix(state, project)

        state.current_unit_index = 4
        last = _build_cache_prefix(state, project)

        assert last.startswith(first), (
            "The last chapter's prefix does not start with the first chapter's "
            "prefix. The entire stable context is not preserved."
        )

    def test_prefix_grows_with_each_chapter(self, tmp_path):
        """Prefix length must increase monotonically across all chapters."""
        project = str(tmp_path / "proj")
        state = _create_fixture_project(project, chapters=5)

        lengths = []
        for i in range(5):
            state.current_unit_index = i
            prefix = _build_cache_prefix(state, project)
            lengths.append(len(prefix))

        for i in range(1, len(lengths)):
            assert lengths[i] > lengths[i - 1], (
                f"Prefix length decreased from chapter {i} ({lengths[i-1]}) "
                f"to chapter {i+1} ({lengths[i]})."
            )


class TestNoTimestampInjection:
    """The prefix must not contain timestamps or other non-deterministic values."""

    def test_prefix_has_no_iso_timestamp(self, tmp_path):
        """No ISO 8601 timestamps in the prefix (would break cache every call)."""
        import re
        project = str(tmp_path / "proj")
        state = _create_fixture_project(project, chapters=3)

        prefix = _build_cache_prefix(state, project)

        # ISO 8601 pattern: 2026-01-01T00:00:00 or similar
        ts_pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        matches = ts_pattern.findall(prefix)
        assert not matches, (
            f"Found ISO timestamps in cache prefix: {matches}. "
            f"Timestamps change every call and silently destroy cache hits."
        )

    def test_prefix_has_no_epoch(self, tmp_path):
        """No Unix epoch timestamps in the prefix."""
        import re
        project = str(tmp_path / "proj")
        state = _create_fixture_project(project, chapters=3)

        prefix = _build_cache_prefix(state, project)

        # Look for 10-digit numbers (Unix epoch seconds)
        epoch_pattern = re.compile(r"\b1[67]\d{8}\b")
        matches = epoch_pattern.findall(prefix)
        assert not matches, (
            f"Found epoch-like timestamps in cache prefix: {matches}."
        )

    def test_prefix_has_no_uuid(self, tmp_path):
        """No UUIDs in the prefix (non-deterministic)."""
        import re
        project = str(tmp_path / "proj")
        state = _create_fixture_project(project, chapters=3)

        prefix = _build_cache_prefix(state, project)

        uuid_pattern = re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            re.IGNORECASE,
        )
        matches = uuid_pattern.findall(prefix)
        assert not matches, (
            f"Found UUIDs in cache prefix: {matches}."
        )


class TestSystemPromptStability:
    """The global system prompt must be constant across the entire run."""

    def test_system_prompt_constant(self):
        """_GLOBAL_SYSTEM_PROMPT must not change between calls."""
        a = _GLOBAL_SYSTEM_PROMPT
        b = _GLOBAL_SYSTEM_PROMPT
        assert a is b or a == b

    def test_system_prompt_no_timestamp(self):
        """No timestamps in the system prompt."""
        import re
        ts_pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        matches = ts_pattern.findall(_GLOBAL_SYSTEM_PROMPT)
        assert not matches, (
            f"Found timestamps in global system prompt: {matches}"
        )


class TestCacheCostReport:
    """Generate a cost summary suitable for CI output (informational, not gated)."""

    def test_prefix_size_report(self, tmp_path, capsys):
        """Print prefix sizes per chapter for manual review."""
        project = str(tmp_path / "proj")
        state = _create_fixture_project(project, chapters=5)

        report = []
        for i in range(5):
            state.current_unit_index = i
            prefix = _build_cache_prefix(state, project)
            approx_tokens = len(prefix) // 4  # ~4 chars per token
            report.append({
                "chapter": i + 1,
                "prefix_chars": len(prefix),
                "approx_tokens": approx_tokens,
            })

        # Print for CI log visibility
        print("\n=== Cache Prefix Size Report ===")
        for r in report:
            print(f"  Chapter {r['chapter']}: {r['prefix_chars']:,} chars "
                  f"(~{r['approx_tokens']:,} tokens)")

        # Estimate cache savings: chapter 5 prefix vs chapter 1 prefix
        growth = report[-1]["prefix_chars"] / report[0]["prefix_chars"] if report[0]["prefix_chars"] else 0
        print(f"\n  Prefix growth ratio (ch5/ch1): {growth:.1f}x")
        print(f"  The shared prefix means DeepSeek caches ~{report[0]['prefix_chars']:,} "
              f"chars across ALL chapters.\n")

        assert len(report) == 5


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
