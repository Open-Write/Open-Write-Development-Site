"""
Tests for the rewritten _make_model_call — Deploy 3a.

Uses httpx.MockTransport to stub the HTTP layer. Each test executes the
real _make_model_call._execute function against canned responses.
"""

import sys
import os
import asyncio
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest

from app.routers.pipeline_router import _make_model_call, _ModelCallFailure, _TruncationError
from app.ai.call_recorder import CallLogWriter


# ── Helpers ──────────────────────────────────────────────────────────────────

def _canned_response(status=200, json_body=None, headers=None):
    """Build a canned httpx.Response."""
    return httpx.Response(
        status_code=status,
        json=json_body,
        headers=headers or {},
    )


def _ok_response(content="Hello!", finish_reason="stop", reasoning_content=""):
    """Build a successful chat completion response."""
    return _canned_response(200, {
        "choices": [{
            "message": {"content": content, "reasoning_content": reasoning_content},
            "finish_reason": finish_reason,
        }],
    })


def _make_mock_transport(responses):
    """Return an httpx.MockTransport that yields canned responses in order."""
    responses = list(responses)  # copy so we can pop

    def handler(request):
        if not responses:
            return _canned_response(500, {"error": {"message": "no more responses"}})
        return responses.pop(0)
    return httpx.MockTransport(handler)


def _run(coro):
    """Run a coroutine."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Test: each failure class ─────────────────────────────────────────────────

def test_class_1_network_transient():
    """ConnectError → NETWORK_TRANSIENT → retry with backoff."""
    # We can't easily mock a ConnectError with MockTransport, so test via
    # the classifier directly and verify the retry loop structure.
    from app.ai.failure_classifier import classify_exception
    exc = httpx.ConnectError("connection refused")
    cls = classify_exception(exc)
    assert cls.failure_class.name == "NETWORK_TRANSIENT"
    assert cls.max_attempts == 5
    assert cls.backoff_schedule[3] == 300  # the long wait


def test_class_2_rate_limit():
    """HTTP 429 → RATE_LIMIT → retry with backoff."""
    transport = _make_mock_transport([
        _canned_response(429, {"error": {"message": "rate limited"}}, {"retry-after": "5"}),
        _ok_response("recovered"),
    ])
    call = _make_model_call("key", "model", "https://openrouter.ai/api/v1", _transport=transport)
    result = _run(call("system", "user"))
    assert result == "recovered"


def test_class_3_server_error():
    """HTTP 500 → SERVER_ERROR → retry with backoff."""
    transport = _make_mock_transport([
        _canned_response(500, {"error": {"message": "server error"}}),
        _ok_response("recovered"),
    ])
    call = _make_model_call("key", "model", "https://openrouter.ai/api/v1", _transport=transport)
    result = _run(call("system", "user"))
    assert result == "recovered"


def test_class_4_auth_payment():
    """HTTP 401 → AUTH_PAYMENT → halt immediately."""
    transport = _make_mock_transport([
        _canned_response(401, {"error": {"message": "invalid key"}}),
    ])
    call = _make_model_call("key", "model", "https://openrouter.ai/api/v1", _transport=transport)
    with pytest.raises(httpx.HTTPStatusError):
        _run(call("system", "user"))


def test_class_5_refusal_mimo():
    """finish_reason=content_filter → REFUSAL → halt (no switch in 3a)."""
    transport = _make_mock_transport([
        _canned_response(200, {
            "choices": [{
                "message": {"content": "The request was rejected because it was considered high risk"},
                "finish_reason": "content_filter",
            }],
        }),
    ])
    call = _make_model_call("key", "model", "https://openrouter.ai/api/v1", _transport=transport)
    with pytest.raises(_ModelCallFailure) as exc_info:
        _run(call("system", "user"))
    assert exc_info.value.failure_class.name == "REFUSAL"


def test_class_5_refusal_zai():
    """finish_reason=stop + refusal content → REFUSAL → halt."""
    transport = _make_mock_transport([
        _canned_response(200, {
            "choices": [{
                "message": {"content": "I cannot fulfill this request. I am programmed to follow strict safety guidelines."},
                "finish_reason": "stop",
            }],
        }),
    ])
    call = _make_model_call("key", "model", "https://openrouter.ai/api/v1", _transport=transport)
    with pytest.raises(_ModelCallFailure) as exc_info:
        _run(call("system", "user"))
    assert exc_info.value.failure_class.name == "REFUSAL"


def test_class_6_truncation():
    """finish_reason=length → TRUNCATION → halt, not success."""
    transport = _make_mock_transport([
        _canned_response(200, {
            "choices": [{
                "message": {"content": "", "reasoning_content": "some reasoning"},
                "finish_reason": "length",
            }],
        }),
    ])
    call = _make_model_call("key", "model", "https://openrouter.ai/api/v1", _transport=transport)
    with pytest.raises(_TruncationError):
        _run(call("system", "user"))


def test_class_7_empty_completion():
    """finish_reason=stop + content=null → EMPTY_COMPLETION → halt (no switch in 3a).

    Class 7 has retry_same_allowed=True with content_budget=2. The classifier
    retries twice, then falls through to _ModelCallFailure. We provide 3
    empty-completion responses to cover both retries + the final raise.
    """
    empty_resp = _canned_response(200, {
        "choices": [{"message": {"content": None}, "finish_reason": "stop"}],
    })
    transport = _make_mock_transport([empty_resp, empty_resp, empty_resp])
    call = _make_model_call("key", "model", "https://openrouter.ai/api/v1", _transport=transport)
    with pytest.raises(_ModelCallFailure) as exc_info:
        _run(call("system", "user"))
    assert exc_info.value.failure_class.name == "EMPTY_COMPLETION"


def test_class_0_success():
    """finish_reason=stop + content → OK → return content."""
    transport = _make_mock_transport([
        _ok_response("Hello, world!"),
    ])
    call = _make_model_call("key", "model", "https://openrouter.ai/api/v1", _transport=transport)
    result = _run(call("system", "user"))
    assert result == "Hello, world!"


# ── Test: record written per attempt ─────────────────────────────────────────

def test_record_written_per_attempt():
    """Each attempt writes a CallRecord with correct fields.

    Uses 429 (rate limit) instead of 500 to avoid the 2-second backoff
    in the transport retry loop — 429 uses Retry-After which we set to 0.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        log_writer = CallLogWriter(tmpdir)
        transport = _make_mock_transport([
            _canned_response(429, {"error": {"message": "rate limited"}}, {"retry-after": "0"}),
            _ok_response("recovered"),
        ])
        call = _make_model_call(
            "key", "test-model", "https://openrouter.ai/api/v1",
            provider_id="openrouter", phase="writer",
            call_log=log_writer, _transport=transport,
        )
        result = _run(call("system", "user"))
        assert result == "recovered"

        records = log_writer.read_all()
        assert len(records) == 2

        # First record: rate limit
        r1 = records[0]
        assert r1["failure_class"] == "rate_limit"
        assert r1["attempt"] == 1
        assert r1["model"] == "test-model"
        assert r1["provider"] == "openrouter"
        assert r1["phase"] == "writer"
        assert r1["elapsed_ms"] >= 0  # mock transport is fast; may be 0

        # Second record: success
        r2 = records[1]
        assert r2["failure_class"] == "ok"
        assert r2["attempt"] == 2


# ── Test: transport does not decrement quality budget ────────────────────────

def test_transport_does_not_decrement_quality_budget():
    """Transport retries don't touch content_budget or quality_budget.
    This is a structural test: the code has separate counters."""
    from app.routers.pipeline_router import _make_model_call as mmc
    import inspect

    source = inspect.getsource(mmc)
    # Verify three independent counters exist
    assert "transport_budget = 5" in source
    assert "content_budget = 2" in source
    # Verify quality_attempts is not referenced (it's managed by orchestrator)
    assert "quality_budget" not in source


# ── Test: None content produces class 7, not empty string ────────────────────

def test_none_not_coerced_to_empty():
    """content=null produces class 7 (EMPTY_COMPLETION), not an empty string.

    Provide 3 responses for the content retry loop (budget=2 + final raise).
    """
    empty_resp = _canned_response(200, {
        "choices": [{"message": {"content": None}, "finish_reason": "stop"}],
    })
    transport = _make_mock_transport([empty_resp, empty_resp, empty_resp])
    call = _make_model_call("key", "model", "https://openrouter.ai/api/v1", _transport=transport)
    with pytest.raises(_ModelCallFailure) as exc_info:
        _run(call("system", "user"))
    assert exc_info.value.failure_class.name == "EMPTY_COMPLETION"


# ── Test: tracking headers conditional on OpenRouter ─────────────────────────

def test_tracking_headers_only_for_openrouter():
    """OpenRouter tracking headers are only sent to OpenRouter URLs."""
    # This is a structural test — verify the conditional exists.
    from app.routers.pipeline_router import _make_model_call as mmc
    import inspect

    source = inspect.getsource(mmc)
    assert "openrouter.ai" in source or "OPENROUTER_BASE" in source
    assert "HTTP-Referer" in source
    assert "X-Title" in source


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
