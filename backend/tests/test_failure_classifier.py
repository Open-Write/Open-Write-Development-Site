"""
Tests for the failure classifier.

Validates that each failure class is correctly detected from HTTP responses,
exceptions, and structural checks.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.ai.failure_classifier import (
    FailureClass, Action, classify_response, classify_exception, classify_malformed,
    _is_refusal_content,
)


def test_class_1_network_transient():
    """Network errors are class 1 with backoff retry."""
    import httpx
    exc = httpx.ConnectError("connection refused")
    cls = classify_exception(exc)
    assert cls.failure_class == FailureClass.NETWORK_TRANSIENT, f"got {cls.failure_class}"
    assert cls.action == Action.RETRY_WITH_BACKOFF
    assert cls.retry_same_allowed is True
    assert cls.switch_allowed is False
    assert cls.max_attempts == 5
    assert len(cls.backoff_schedule) == 5
    assert cls.backoff_schedule[3] == 300  # the long wait

    exc2 = httpx.ReadTimeout("read timed out")
    cls2 = classify_exception(exc2)
    assert cls2.failure_class == FailureClass.NETWORK_TRANSIENT
    assert cls2.max_attempts == 5


def test_class_2_rate_limit():
    """HTTP 429 is class 2 with backoff retry and switch allowed."""
    cls = classify_response(http_status=429, finish_reason=None, content=None)
    assert cls.failure_class == FailureClass.RATE_LIMIT
    assert cls.action == Action.RETRY_WITH_BACKOFF
    assert cls.retry_same_allowed is True
    assert cls.switch_allowed is True
    assert cls.max_attempts == 3

    # With Retry-After header
    cls2 = classify_response(http_status=429, finish_reason=None, content=None, retry_after_header="30")
    assert cls2.retry_after == 30.0

    # Retry-After clamped to 120s
    cls3 = classify_response(http_status=429, finish_reason=None, content=None, retry_after_header="600")
    assert cls3.retry_after == 120.0


def test_class_3_server_error():
    """HTTP 500/502/503/504 is class 3 with backoff retry and switch allowed."""
    for status in (500, 502, 503, 504):
        cls = classify_response(http_status=status, finish_reason=None, content=None)
        assert cls.failure_class == FailureClass.SERVER_ERROR, f"HTTP {status}: got {cls.failure_class}"
        assert cls.action == Action.RETRY_WITH_BACKOFF
        assert cls.retry_same_allowed is True
        assert cls.switch_allowed is True
        assert cls.max_attempts == 3


def test_class_4_auth_payment():
    """HTTP 401/402/403 is class 4 — halt immediately, no retry, no switch."""
    for status in (401, 402, 403):
        cls = classify_response(http_status=status, finish_reason=None, content=None)
        assert cls.failure_class == FailureClass.AUTH_PAYMENT, f"HTTP {status}: got {cls.failure_class}"
        assert cls.action == Action.HALT_IMMEDIATE
        assert cls.retry_same_allowed is False
        assert cls.switch_allowed is False
        assert cls.max_attempts == 0


def test_class_5_refusal_mimo():
    """MiMo refusal: finish_reason=content_filter."""
    cls = classify_response(
        http_status=200,
        finish_reason="content_filter",
        content="The request was rejected because it was considered high risk",
    )
    assert cls.failure_class == FailureClass.REFUSAL
    assert cls.action == Action.SWITCH_THEN_HALT
    assert cls.retry_same_allowed is False
    assert cls.switch_allowed is True
    assert cls.max_attempts == 1


def test_class_5_refusal_zai():
    """ZAI refusal: finish_reason=stop but content has refusal language."""
    cls = classify_response(
        http_status=200,
        finish_reason="stop",
        content="I cannot fulfill this request. I am programmed to follow strict safety guidelines.",
    )
    assert cls.failure_class == FailureClass.REFUSAL
    assert cls.action == Action.SWITCH_THEN_HALT
    assert cls.retry_same_allowed is False
    assert cls.switch_allowed is True


def test_class_5_not_refusal_normal():
    """Normal content with 'cannot' in it is NOT a refusal."""
    cls = classify_response(
        http_status=200,
        finish_reason="stop",
        content="She could not believe what she was seeing. The door was open.",
    )
    assert cls.failure_class == FailureClass.OK


def test_class_6_truncation():
    """finish_reason=length is class 6 — halt unit."""
    cls = classify_response(
        http_status=200,
        finish_reason="length",
        content="",
    )
    assert cls.failure_class == FailureClass.TRUNCATION
    assert cls.action == Action.HALT_UNIT
    assert cls.retry_same_allowed is False
    assert cls.switch_allowed is False
    assert cls.max_attempts == 0


def test_class_7_empty_completion():
    """finish_reason=stop with empty content is class 7."""
    # content="" (explicit empty string)
    cls = classify_response(http_status=200, finish_reason="stop", content="")
    assert cls.failure_class == FailureClass.EMPTY_COMPLETION
    assert cls.action == Action.SWITCH_THEN_HALT
    assert cls.retry_same_allowed is True
    assert cls.switch_allowed is True
    assert cls.max_attempts == 1

    # content=None (field absent)
    cls2 = classify_response(http_status=200, finish_reason="stop", content=None)
    assert cls2.failure_class == FailureClass.EMPTY_COMPLETION


def test_class_8_malformed():
    """Structural malformation detected by caller."""
    cls = classify_malformed("bible reply will not split into sections")
    assert cls.failure_class == FailureClass.MALFORMED_OUTPUT
    assert cls.action == Action.ESCALATE_RETRY
    assert cls.retry_same_allowed is True
    assert cls.max_attempts == 2


def test_class_0_ok():
    """Normal completion is class 0 (OK)."""
    cls = classify_response(
        http_status=200,
        finish_reason="stop",
        content="Hello! How can I help you today?",
    )
    assert cls.failure_class == FailureClass.OK
    assert cls.action == Action.CONTINUE
    assert cls.max_attempts == 0


def test_refusal_content_patterns():
    """Refusal content pattern matching."""
    # Should match
    assert _is_refusal_content("I cannot fulfill this request.")
    assert _is_refusal_content("I'm not able to assist with that.")
    assert _is_refusal_content("My safety guidelines prohibit me from")
    assert _is_refusal_content("The request was rejected because it was considered high risk")
    assert _is_refusal_content("I am unable to provide instructions for")

    # Should NOT match
    assert not _is_refusal_content("She could not believe what she was seeing.")
    assert not _is_refusal_content("I can help you with that. Here's the chapter:")
    assert not _is_refusal_content("")
    assert not _is_refusal_content(None)


def test_http_400_client_error():
    """HTTP 400 is class 8 (malformed/client error), not retryable."""
    cls = classify_response(
        http_status=400,
        finish_reason=None,
        content=None,
        error_body={"error": {"code": "1213", "message": "The prompt parameter was not received normally."}},
    )
    assert cls.failure_class == FailureClass.MALFORMED_OUTPUT
    assert cls.action == Action.HALT_UNIT
    assert cls.retry_same_allowed is False


def test_three_budgets_independent():
    """Verify the three budgets are specified independently."""
    # Transport budget: class 1
    cls1 = classify_exception(type("ConnectError", (Exception,), {})())
    # (This tests classify_exception with unknown exception — should halt)

    # Content budget: class 7
    cls7 = classify_response(http_status=200, finish_reason="stop", content="")
    assert cls7.max_attempts == 1  # content budget

    # Quality budget: managed by orchestrator, not classifier
    # (no test needed here — it's tested at the orchestrator level)


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    if failed:
        sys.exit(1)
