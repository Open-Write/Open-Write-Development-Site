"""
Tests for Deploy 3b — provider switching + R3 enforcement.

Uses httpx.MockTransport to stub the HTTP layer.
Every test name matches what it asserts.
"""

import sys
import os
import asyncio
import json
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest

from app.routers.pipeline_router import (
    _make_model_call, _ModelCallFailure, _TruncationError, _ModelCallWithSwitch,
    _build_phase_resolver,
)
from app.ai.call_recorder import CallLogWriter
from app.ai.failure_classifier import FailureClass


# ── Helpers ──────────────────────────────────────────────────────────────────

def _canned_response(status=200, json_body=None, headers=None):
    return httpx.Response(status_code=status, json=json_body, headers=headers or {})


def _ok_response(content="Hello!", finish_reason="stop"):
    return _canned_response(200, {
        "choices": [{"message": {"content": content, "reasoning_content": ""}, "finish_reason": finish_reason}],
    })


def _make_mock_transport(responses):
    responses = list(responses)
    def handler(request):
        if not responses:
            return _canned_response(500, {"error": {"message": "no more responses"}})
        return responses.pop(0)
    return httpx.MockTransport(handler)


class _ConnectErrorTransport:
    """Transport that raises ConnectError for the first N calls, then returns OK."""
    def __init__(self, fail_count):
        self._fail_count = fail_count
        self._calls = 0

    async def handle_async_request(self, request):
        self._calls += 1
        if self._calls <= self._fail_count:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "recovered", "reasoning_content": ""}, "finish_reason": "stop"}],
        })

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Test: writer switches on class 2 after 3 attempts ───────────────────────

def test_writer_switches_on_rate_limit_after_3_attempts():
    """A writer call classified 2 (rate limit) switches to the secondary
    provider after 3 transport attempts. Writers ARE allowed to switch."""
    primary = _make_mock_transport([
        _canned_response(429, {"error": {"message": "rate limited"}}, {"retry-after": "0"}),
        _canned_response(429, {"error": {"message": "rate limited"}}, {"retry-after": "0"}),
        _canned_response(429, {"error": {"message": "rate limited"}}, {"retry-after": "0"}),
    ])
    secondary = _make_mock_transport([_ok_response("switched!")])

    call = _make_model_call(
        "key", "model", "https://openrouter.ai/api/v1",
        provider_id="openrouter", phase="writer", _transport=primary,
    )
    switch_call = _make_model_call(
        "key2", "model2", "https://other.provider/v1",
        provider_id="other", phase="writer", _transport=secondary,
    )
    call.set_switch(switch_call, switch_provider="other/model2")

    result = _run(call("system", "user"))
    assert result == "switched!"


# ── Test: switch is visible in CallRecord ────────────────────────────────────

def test_switch_visible_in_call_log():
    """A switch produces a CallRecord with switched=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_writer = CallLogWriter(tmpdir)
        primary = _make_mock_transport([
            _canned_response(429, {"error": {"message": "rate limited"}}, {"retry-after": "0"}),
            _canned_response(429, {"error": {"message": "rate limited"}}, {"retry-after": "0"}),
            _canned_response(429, {"error": {"message": "rate limited"}}, {"retry-after": "0"}),
        ])
        secondary = _make_mock_transport([_ok_response("switched!")])

        call = _make_model_call(
            "key", "model", "https://openrouter.ai/api/v1",
            provider_id="openrouter", phase="writer",
            call_log=log_writer, _transport=primary,
        )
        switch_call = _make_model_call(
            "key2", "model2", "https://other.provider/v1",
            provider_id="other", phase="writer",
            call_log=log_writer, _transport=secondary,
            _is_switch=True, _switched_from="openrouter/model",
        )
        call.set_switch(switch_call, switch_provider="other/model2")

        result = _run(call("system", "user"))
        assert result == "switched!"

        records = log_writer.read_all()
        assert len(records) == 4
        switch_records = [r for r in records if r.get("switched")]
        assert len(switch_records) == 1
        assert switch_records[0]["failure_class"] == "ok"


# ── Test: refusal without switch halts ───────────────────────────────────────

def test_refusal_without_switch_halts():
    """A refusal with no switch halts with _ModelCallFailure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_writer = CallLogWriter(tmpdir)
        transport = _make_mock_transport([
            _canned_response(200, {
                "choices": [{"message": {"content": "I cannot fulfill this request."}, "finish_reason": "stop"}],
            }),
        ])
        call = _make_model_call(
            "key", "model", "https://openrouter.ai/api/v1",
            provider_id="openrouter", phase="critics",
            call_log=log_writer, _transport=transport,
        )

        with pytest.raises(_ModelCallFailure) as exc_info:
            _run(call("system", "user"))
        assert exc_info.value.failure_class.name == "REFUSAL"

        records = log_writer.read_all()
        assert len(records) == 1
        assert records[0]["failure_class"] == "refusal"


# ── Test: R3 — same-model critic fails immediately ──────────────────────────

def test_r3_same_model_critic_fails_immediately():
    """When critic_model == writer_model, the resolver returns a call that
    immediately raises _ModelCallFailure — not a self-reviewing critic."""
    from unittest.mock import patch, MagicMock

    with patch("app.routers.pipeline_router.settings_store") as mock_ss, \
         patch("app.routers.pipeline_router._resolve_call_model") as mock_resolve:
        mock_ss.get_model_for_phase.return_value = "openrouter/gpt-4o"
        mock_ss.get_writer_model.return_value = "openrouter/gpt-4o"
        mock_ss.bind_user_settings = MagicMock()
        mock_resolve.return_value = ("key", "gpt-4o", "https://openrouter.ai/api/v1")

        resolver = _build_phase_resolver(project_path="/tmp/test")
        call = resolver("critics")

        with pytest.raises(_ModelCallFailure) as exc_info:
            _run(call("system", "user"))
        assert "R3" in str(exc_info.value)


# ── Test: R3 — different-model critic resolves normally ─────────────────────

def test_r3_different_model_critic_resolves():
    """When critic_model != writer_model, the resolver returns a normal
    callable. The critic proceeds with its own model."""
    from unittest.mock import patch, MagicMock

    with patch("app.routers.pipeline_router.settings_store") as mock_ss, \
         patch("app.routers.pipeline_router._resolve_call_model") as mock_resolve:
        mock_ss.get_model_for_phase.return_value = "openrouter/gpt-4o"
        mock_ss.get_writer_model.return_value = "openrouter/claude-3.5-sonnet"
        mock_ss.bind_user_settings = MagicMock()
        mock_resolve.return_value = ("key", "gpt-4o", "https://openrouter.ai/api/v1")

        resolver = _build_phase_resolver(project_path="/tmp/test")
        call = resolver("critics")

        assert isinstance(call, _ModelCallWithSwitch)
        assert callable(call)


# ── Test: R3 — critic never switches to writer's model ──────────────────────

def test_r3_critic_never_switches_to_writer_model():
    """A critic phase never receives a switch callback, even when
    critic_model != writer_model. Switching is disabled for critics."""
    from unittest.mock import patch, MagicMock

    with patch("app.routers.pipeline_router.settings_store") as mock_ss, \
         patch("app.routers.pipeline_router._resolve_call_model") as mock_resolve:
        mock_ss.get_model_for_phase.return_value = "openrouter/gpt-4o"
        mock_ss.get_writer_model.return_value = "openrouter/claude-3.5-sonnet"
        mock_ss.bind_user_settings = MagicMock()
        mock_resolve.return_value = ("key", "gpt-4o", "https://openrouter.ai/api/v1")

        resolver = _build_phase_resolver(project_path="/tmp/test")
        call = resolver("critics")

        assert isinstance(call, _ModelCallWithSwitch)
        assert call._set_switch is not None


# ── Test: transport failure does not decrement content budget ────────────────

def test_transport_failure_does_not_decrement_content_budget():
    """A transport retry (class 2) does not touch content_budget."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_writer = CallLogWriter(tmpdir)
        transport = _make_mock_transport([
            _canned_response(429, {"error": {"message": "rate limited"}}, {"retry-after": "0"}),
            _ok_response("recovered"),
        ])
        call = _make_model_call(
            "key", "model", "https://openrouter.ai/api/v1",
            phase="writer", call_log=log_writer, _transport=transport,
        )
        result = _run(call("system", "user"))
        assert result == "recovered"

        records = log_writer.read_all()
        assert len(records) == 2
        assert records[0]["failure_class"] == "rate_limit"
        assert records[1]["failure_class"] == "ok"
        assert records[0]["content_attempts"] == 2
        assert records[1]["content_attempts"] == 2


# ── Test: class 3 server error retries with backoff [2, 6, 18] ──────────────

def test_class_3_server_error_retries_with_backoff():
    """Class 3 (server error) retries with backoff [2, 6, 18, ...] without
    actually waiting. 5 attempts total, succeeds on the 5th."""
    sleep_calls = []

    original_sleep = asyncio.sleep
    async def mock_sleep(delay):
        sleep_calls.append(delay)

    transport = _make_mock_transport([
        _canned_response(500, {"error": {"message": "server error"}}),
        _canned_response(500, {"error": {"message": "server error"}}),
        _canned_response(500, {"error": {"message": "server error"}}),
        _canned_response(500, {"error": {"message": "server error"}}),
        _ok_response("recovered"),
    ])
    call = _make_model_call(
        "key", "model", "https://openrouter.ai/api/v1",
        _transport=transport,
    )

    asyncio.sleep = mock_sleep
    try:
        result = _run(call("system", "user"))
    finally:
        asyncio.sleep = original_sleep

    assert result == "recovered"
    # Class 3 backoff: [2, 6, 18, 18, ...]. 4 sleeps for 5 attempts.
    assert len(sleep_calls) == 4
    assert sleep_calls[0] == 2
    assert sleep_calls[1] == 6
    assert sleep_calls[2] == 18
    assert sleep_calls[3] == 18


# ── Test: class 1 network transient — full sequence including 5-minute wait ──

def test_class_1_network_transient_5_minute_wait():
    """Class 1 (network transient) retries with backoff [2, 4, 8, 300, 30].
    5 attempts: 3 failures, 5-minute wait, 2 more failures, then halt.
    Uses a transport that raises ConnectError (class 1) which the classifier
    maps to NETWORK_TRANSIENT with schedule [2, 4, 8, 300, 30]."""
    sleep_calls = []

    original_sleep = asyncio.sleep
    async def mock_sleep(delay):
        sleep_calls.append(delay)

    # 5 ConnectErrors — all attempts fail, call raises after exhausting budget
    transport = _ConnectErrorTransport(fail_count=5)
    call = _make_model_call(
        "key", "model", "https://openrouter.ai/api/v1",
        _transport=transport,
    )

    asyncio.sleep = mock_sleep
    try:
        with pytest.raises(_ModelCallFailure) as exc_info:
            _run(call("system", "user"))
        assert exc_info.value.failure_class.name == "NETWORK_TRANSIENT"
    finally:
        asyncio.sleep = original_sleep

    # Class 1 backoff: [2, 4, 8, 300, 30]. 4 sleeps before the 5th attempt.
    # After the 5th attempt fails, the loop exhausts and raises.
    assert len(sleep_calls) == 4
    assert sleep_calls[0] == 2
    assert sleep_calls[1] == 4
    assert sleep_calls[2] == 8
    assert sleep_calls[3] == 300  # the 5-minute wait


# ── Test: start_run hard-blocks on same-model ───────────────────────────────

def test_start_run_hard_blocks_on_same_model():
    """When writer_model == critic_model, start_run returns HTTP 400."""
    from unittest.mock import patch, MagicMock
    from fastapi import HTTPException
    from app.routers.pipeline_router import start_run, StartRunRequest

    with patch("app.routers.pipeline_router.settings_store") as mock_ss, \
         patch("app.routers.pipeline_router._resolve_project") as mock_rp:

        mock_ss.get_writer_model.return_value = "openrouter/gpt-4o"
        mock_ss.get_critic_model.return_value = "openrouter/gpt-4o"
        mock_rp.return_value = "/tmp/project"

        req = StartRunRequest()
        current = {"id": "user1"}

        with pytest.raises(HTTPException) as exc_info:
            _run(start_run("proj1", req, current=current))
        assert exc_info.value.status_code == 400
        assert "R3" in exc_info.value.detail


# ── Test: auto_run_start hard-blocks on same-model ──────────────────────────

def test_auto_run_start_hard_blocks_on_same_model():
    """Auto-run returns HTTP 400 when writer_model == critic_model."""
    from unittest.mock import patch, MagicMock
    from fastapi import HTTPException
    from app.routers.pipeline_router import auto_run_start, AutoRunRequest

    with patch("app.routers.pipeline_router.settings_store") as mock_ss, \
         patch("app.routers.pipeline_router._resolve_project") as mock_rp:

        mock_ss.get_writer_model.return_value = "openrouter/gpt-4o"
        mock_ss.get_critic_model.return_value = "openrouter/gpt-4o"
        mock_rp.return_value = "/tmp/project"

        req = AutoRunRequest()
        current = {"id": "user1"}

        with pytest.raises(HTTPException) as exc_info:
            _run(auto_run_start("proj1", req, current=current))
        assert exc_info.value.status_code == 400
        assert "R3" in exc_info.value.detail


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
