"""
free_tier_guard.py — Per-user token caps for free-tier providers.

When routing to a free-tier provider (e.g. GLM free tier), unlimited usage
exposes the service to rate-limit throttling and unfunded promises. This
module provides:

  1. Per-user, per-provider token tracking (in-memory, resets on restart).
  2. Hard caps that prevent overuse — when the cap is hit, the guard signals
     the caller to fail over to a paid provider rather than failing the request.
  3. A kill switch (FREE_TIER_DISABLED env var) that instantly routes all
     free-tier traffic to paid providers.

This is NOT a billing system. It's a safety valve: the exposure is a surprise
rate-limit and a promise you can't fund, not a surprise invoice.

Usage in pipeline_router._make_model_call:
    if free_tier_guard.should_block(user_id, provider_id):
        # skip this provider, use fallback

Cap is per-user per-month (resets when the process restarts, which is
acceptable for a single-server deployment on Railway).
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

# Providers that are free-tier and need caps.
FREE_TIER_PROVIDERS = {"glm", "zai"}

# Default monthly token cap per user per free-tier provider.
# At ~4 chars/token, 500K tokens ≈ 2M chars ≈ ~500K words of input.
# That's roughly 6 full novels of pipeline input — generous but bounded.
DEFAULT_MONTHLY_TOKEN_CAP = int(os.environ.get("FREE_TIER_MONTHLY_CAP", "500000"))

# Kill switch: set to "1" to disable all free-tier routing immediately.
FREE_TIER_DISABLED = os.environ.get("FREE_TIER_DISABLED", "0") == "1"


# ── In-memory tracker ───────────────────────────────────────────────────────

@dataclass
class _UserUsage:
    tokens_used: int = 0
    month: str = ""  # "YYYY-MM" for the current tracking period


# Key: (user_id, provider_id) -> _UserUsage
_usage: dict[tuple[str, str], _UserUsage] = {}
_lock = threading.Lock()


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def record_usage(user_id: str, provider_id: str, tokens: int) -> None:
    """Record token usage for a user on a free-tier provider.

    Called after a successful API call. Resets the counter if the month
    has rolled over.
    """
    if provider_id not in FREE_TIER_PROVIDERS:
        return
    key = (user_id, provider_id)
    month = _current_month()
    with _lock:
        entry = _usage.get(key)
        if entry is None:
            entry = _UserUsage()
            _usage[key] = entry
        if entry.month != month:
            entry.tokens_used = 0
            entry.month = month
        entry.tokens_used += tokens


def tokens_remaining(user_id: str, provider_id: str) -> int:
    """Return how many tokens the user has left on this free-tier provider this month."""
    if provider_id not in FREE_TIER_PROVIDERS:
        return DEFAULT_MONTHLY_TOKEN_CAP
    key = (user_id, provider_id)
    month = _current_month()
    with _lock:
        entry = _usage.get(key)
        if entry is None or entry.month != month:
            return DEFAULT_MONTHLY_TOKEN_CAP
        return max(0, DEFAULT_MONTHLY_TOKEN_CAP - entry.tokens_used)


def should_block(user_id: str, provider_id: str) -> bool:
    """Return True if the user has exhausted their free-tier allowance.

    Also returns True if the global kill switch is active.
    """
    if FREE_TIER_DISABLED:
        return True
    if provider_id not in FREE_TIER_PROVIDERS:
        return False
    return tokens_remaining(user_id, provider_id) <= 0


def usage_summary(user_id: str) -> dict:
    """Return a summary of the user's free-tier usage (for the UI/debugging)."""
    month = _current_month()
    summary = {}
    for pid in FREE_TIER_PROVIDERS:
        key = (user_id, pid)
        with _lock:
            entry = _usage.get(key)
            used = entry.tokens_used if entry and entry.month == month else 0
        remaining = max(0, DEFAULT_MONTHLY_TOKEN_CAP - used)
        summary[pid] = {
            "tokens_used": used,
            "tokens_remaining": remaining,
            "monthly_cap": DEFAULT_MONTHLY_TOKEN_CAP,
            "pct_used": round(used / DEFAULT_MONTHLY_TOKEN_CAP * 100, 1) if DEFAULT_MONTHLY_TOKEN_CAP else 0,
            "kill_switch_active": FREE_TIER_DISABLED,
        }
    return summary
