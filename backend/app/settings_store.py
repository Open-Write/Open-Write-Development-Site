"""Web settings store — DB-backed, per-request, contextvar-scoped.

This module is a drop-in replacement for the desktop app's file-based
``app.settings_store``. The pipeline and AI modules (copied verbatim) import
functions like ``get_providers`` / ``get_writer_model`` from here. In the web
app, settings are per-user and live in the ``user_settings`` PostgreSQL table.

How the wiring works
--------------------
Every authenticated request calls ``bind_user_settings(user_id)`` (from the
auth dependency). That loads the user's settings dict from the DB and stores
it in a :class:`contextvars.ContextVar`. Because the pipeline runs inside the
same request coroutine, the contextvar propagates across every ``await`` and
all the ``get_*`` helpers below resolve to the current user's API keys.

The public function surface intentionally mirrors the desktop module so no
pipeline/AI code needs editing.
"""
from __future__ import annotations

import contextvars
import json
import os

from app import db

# Server-side API keys — injected into all users' providers so they
# can use the service without providing their own key. Set via
# environment variables on Railway.
_DEEPSEEK_SERVER_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
_MIMO_SERVER_KEY = os.environ.get("MIMO_API_KEY", "")

# Account tier → allowed model prefixes. Basic tier gets the two cheapest
# models; pro/admin get everything.
TIER_MODEL_ALLOWLIST = {
    "basic": [
        "deepseek/deepseek-v4-flash",
        "mimo/mimo-v2.5",
    ],
    "pro": None,  # None = all models allowed
    "admin": None,
}

# Monthly token allowance per tier (input + output tokens combined).
TIER_MONTHLY_TOKENS = {
    "basic": 7_500_000,
    "pro": 15_000_000,
    "admin": 100_000_000,
}

# Per-request holder of the current user's raw settings dict (or None).
_current_settings: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "current_settings", default=None
)
_current_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_id", default=None
)


# ── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_SETTINGS: dict = {
    "openrouter_api_key": "",
    "default_model": "deepseek/deepseek-v4-flash",
    "providers": [],
    "writer_model": "deepseek/deepseek-v4-flash",
    "critic_model": "deepseek/deepseek-v4-flash",
    "model_routing": {},
    "fallback_providers": [],
    "planner_model": "",
    "audiobook_model": "",
    "content_mode": "general",
    "cost_tier": "standard",
    "text_only_filter": True,
    "starred_models": [],
    "model_allowlist": [],
    "model_blocklist": [],
    "model_content_modes": {},
    "vault_root": "/data/openwrite_data",
    "theme": "dark",
    "ui_scale": "default",
    "writing_skill_level": "novice",
    "day_rollover_hour": 0,
}


# ── Provider seeds (curated OpenAI-compatible endpoints) ──────────────────────
PROVIDER_SEEDS = [
    {"id": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "api_key": "", "models": []},
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "api_key": "", "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o4-mini"]},
    {"id": "anthropic", "label": "Anthropic", "base_url": "https://api.anthropic.com/v1", "api_key": "", "models": ["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"]},
    {"id": "google", "label": "Google AI", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "api_key": "", "models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]},
    {"id": "deepseek", "label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "api_key": "", "models": ["deepseek-v4-flash", "deepseek-v4-pro"]},
    {"id": "glm", "label": "GLM / Z.AI (Pay-as-you-go)", "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key": "", "models": ["glm-4-plus", "glm-4", "glm-4-flash", "glm-4.6", "glm-4.6-flash"]},
    {"id": "zai", "label": "Z.AI (Coding Plan — Singapore)", "base_url": "https://api.z.ai/api/coding/paas/v4", "api_key": "", "models": ["glm-5.2", "glm-5.2-flash", "glm-5.1", "glm-5.1-flash", "glm-4.6", "glm-4.6-flash", "glm-4.6-thinking", "glm-z1-flash", "glm-4-flashx"]},
    {"id": "mimo", "label": "Xiaomi MiMo (Singapore)", "base_url": "https://token-plan-sgp.xiaomimimo.com/v1", "api_key": "", "models": ["mimo-v2.5-pro", "mimo-v2.5"]},
    {"id": "mistral", "label": "Mistral", "base_url": "https://api.mistral.ai/v1", "api_key": "", "models": ["mistral-large-latest", "mistral-small-latest"]},
    {"id": "groq", "label": "Groq", "base_url": "https://api.groq.com/openai/v1", "api_key": "", "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]},
    {"id": "xai", "label": "xAI", "base_url": "https://api.x.ai/v1", "api_key": "", "models": ["grok-3", "grok-3-mini"]},
]


# ── Contextvar binding ─────────────────────────────────────────────────────────
def bind_user_settings(user_id: str) -> None:
    """Load a user's settings from the DB and install them for this request."""
    _current_user_id.set(str(user_id))
    row = db.query_one(
        "SELECT settings_json FROM user_settings WHERE user_id = %s", (user_id,)
    )
    if row and row.get("settings_json"):
        try:
            parsed = json.loads(row["settings_json"])
            if isinstance(parsed, dict):
                _current_settings.set(parsed)
                return
        except (json.JSONDecodeError, TypeError):
            pass
    _current_settings.set({})


def load_settings() -> dict:
    """Return the current user's settings merged over defaults."""
    current = _current_settings.get() or {}
    return {**DEFAULT_SETTINGS, **current}


def save_settings(settings: dict) -> None:
    """Persist the current user's settings to the DB (and refresh the contextvar)."""
    user_id = _current_user_id.get()
    safe = {k: settings.get(k, v) for k, v in DEFAULT_SETTINGS.items()}
    _current_settings.set(safe)
    if user_id is None:
        return
    db.execute(
        """
        INSERT INTO user_settings (user_id, settings_json, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (user_id) DO UPDATE
          SET settings_json = EXCLUDED.settings_json, updated_at = NOW()
        """,
        (user_id, json.dumps(safe)),
    )


# ── Provider normalization ─────────────────────────────────────────────────────
def _normalize_providers(raw: list) -> list[dict]:
    by_id: dict[str, dict] = {}
    if isinstance(raw, list):
        for p in raw:
            if isinstance(p, dict) and p.get("id"):
                by_id[p["id"]] = {
                    "id": str(p["id"]),
                    "label": str(p.get("label") or p["id"]),
                    "base_url": str(p.get("base_url") or ""),
                    "api_key": str(p.get("api_key") or ""),
                    "models": [str(m) for m in p.get("models", []) if m],
                }
    # URL migration: fix mimo base_url if it was saved with the old wrong Z.AI endpoint
    _MIMO_OLD_URL = "https://api.z.ai/api/coding/paas/v4"
    _MIMO_NEW_URL = "https://token-plan-sgp.xiaomimimo.com/v1"
    if "mimo" in by_id and by_id["mimo"].get("base_url") == _MIMO_OLD_URL:
        by_id["mimo"]["base_url"] = _MIMO_NEW_URL

    # Model list migration: always replace curated model names from seed so
    # stale names saved in the DB (e.g. "MiMo-7B-RL") become the current names
    # (e.g. "mimo-2.5"). Keys are users' live-fetched models from clicking
    # "Fetch models" — those are stored in liveModels in the frontend only and
    # are never persisted here, so overwriting the seed list is safe.
    _REFRESH_MODELS = {"zai", "mimo", "glm"}
    for pid in _REFRESH_MODELS:
        if pid in by_id:
            seed_entry = next((s for s in PROVIDER_SEEDS if s["id"] == pid), None)
            if seed_entry:
                by_id[pid]["models"] = list(seed_entry["models"])
    for seed in PROVIDER_SEEDS:
        existing = by_id.get(seed["id"])
        if existing is None:
            by_id[seed["id"]] = dict(seed)
        else:
            existing.setdefault("label", seed["label"])
            if not existing.get("base_url") and seed["base_url"]:
                existing["base_url"] = seed["base_url"]
            for m in seed["models"]:
                if m not in existing["models"]:
                    existing["models"].append(m)
    ordered = [by_id[s["id"]] for s in PROVIDER_SEEDS if s["id"] in by_id]
    for pid, p in by_id.items():
        if pid not in {s["id"] for s in PROVIDER_SEEDS}:
            ordered.append(p)
    return ordered


def get_providers() -> list[dict]:
    settings = load_settings()
    providers = _normalize_providers(settings.get("providers", []))
    legacy_key = settings.get("openrouter_api_key", "")
    if legacy_key:
        for p in providers:
            if p["id"] == "openrouter" and not p.get("api_key"):
                p["api_key"] = legacy_key
    # Inject server-side keys if the user hasn't set their own.
    # Track which key source is active so the UI can distinguish.
    if _DEEPSEEK_SERVER_KEY:
        for p in providers:
            if p["id"] == "deepseek":
                if not p.get("api_key"):
                    p["api_key"] = _DEEPSEEK_SERVER_KEY
                    p["key_source"] = "openwrite"
                else:
                    p["key_source"] = "user"
    if _MIMO_SERVER_KEY:
        for p in providers:
            if p["id"] == "mimo":
                if not p.get("api_key"):
                    p["api_key"] = _MIMO_SERVER_KEY
                    p["key_source"] = "openwrite"
                    # The Open-Write company key is pay-as-you-go, NOT on the
                    # token plan — it only works against the standard endpoint.
                    p["base_url"] = "https://api.xiaomimimo.com/v1"
                else:
                    p["key_source"] = "user"
    # Mark all other providers with keys as "user" source
    for p in providers:
        if "key_source" not in p:
            p["key_source"] = "user" if p.get("api_key") else "none"
    return providers


def get_server_key_providers() -> list[str]:
    """Return provider IDs that have Open-Write server keys available."""
    result = []
    if _DEEPSEEK_SERVER_KEY:
        result.append("deepseek")
    if _MIMO_SERVER_KEY:
        result.append("mimo")
    return result


# ── Convenience accessors used by pipeline/ai code ─────────────────────────────
def get_api_key() -> str:
    settings = load_settings()
    for p in settings.get("providers", []):
        if isinstance(p, dict) and p.get("id") == "openrouter" and p.get("api_key"):
            return p["api_key"]
    return settings.get("openrouter_api_key", "")


def get_default_model() -> str:
    return load_settings().get("default_model", "") or ""


def get_writer_model() -> str:
    settings = load_settings()
    return settings.get("writer_model", "") or settings.get("default_model", DEFAULT_SETTINGS["default_model"])


def get_critic_model() -> str:
    settings = load_settings()
    return settings.get("critic_model", "") or settings.get("default_model", DEFAULT_SETTINGS["default_model"])


def get_model_for_phase(phase: str) -> str:
    settings = load_settings()
    routing = settings.get("model_routing", {})
    choice = routing.get(phase, "")
    if choice == "critic":
        return get_critic_model()
    if choice == "writer":
        return get_writer_model()
    if phase in ("critics", "editorial"):
        return get_critic_model()
    return get_writer_model()


def get_planner_model() -> str:
    settings = load_settings()
    return settings.get("planner_model", "") or settings.get("default_model", DEFAULT_SETTINGS["default_model"])


def get_audiobook_model() -> str:
    """Return the model to use for audiobook script generation.

    Falls back to the default model if no audiobook-specific model is set.
    """
    settings = load_settings()
    return settings.get("audiobook_model", "") or settings.get("default_model", DEFAULT_SETTINGS["default_model"])


def get_rollover_hour() -> int:
    raw = load_settings().get("day_rollover_hour", 0)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value in (0, 4) else 0


def get_vault_root() -> str:
    return load_settings().get("vault_root", DEFAULT_SETTINGS["vault_root"])


# ── Account tier and token tracking ──────────────────────────────────────────

def get_user_tier(user_id: str) -> str:
    """Return the user's account tier (basic, pro, admin)."""
    row = db.query_one("SELECT account_tier FROM users WHERE id = %s", (user_id,))
    if row and row.get("account_tier"):
        return row["account_tier"]
    return "basic"


def get_allowed_models(user_id: str) -> list[str] | None:
    """Return the list of allowed model IDs for the user's tier.

    Returns None if all models are allowed (pro/admin).
    """
    tier = get_user_tier(user_id)
    return TIER_MODEL_ALLOWLIST.get(tier)


def get_monthly_token_allowance(user_id: str) -> int:
    """Return the monthly token allowance for the user's tier."""
    tier = get_user_tier(user_id)
    return TIER_MONTHLY_TOKENS.get(tier, TIER_MONTHLY_TOKENS["basic"])


def get_token_usage(user_id: str) -> dict:
    """Return the user's token usage for the current billing period.

    Returns {tokens_used, tokens_remaining, monthly_allowance, reset_date, tier}.
    """
    tier = get_user_tier(user_id)
    allowance = TIER_MONTHLY_TOKENS.get(tier, TIER_MONTHLY_TOKENS["basic"])

    # Get usage from the token_usage table for the current month.
    row = db.query_one(
        "SELECT COALESCE(SUM(tokens_used), 0) as total "
        "FROM token_usage WHERE user_id = %s AND period_start <= NOW() "
        "AND period_end > NOW()",
        (user_id,),
    )
    used = int(row["total"]) if row else 0

    # Get the reset date (end of current billing period).
    reset_row = db.query_one(
        "SELECT period_end FROM token_usage WHERE user_id = %s "
        "AND period_start <= NOW() AND period_end > NOW() "
        "ORDER BY period_end LIMIT 1",
        (user_id,),
    )
    reset_date = reset_row["period_end"].isoformat() if reset_row and reset_row.get("period_end") else None

    return {
        "tokens_used": used,
        "tokens_remaining": max(0, allowance - used),
        "monthly_allowance": allowance,
        "reset_date": reset_date,
        "tier": tier,
    }


def record_token_usage(user_id: str, tokens: int) -> None:
    """Record token usage for the current billing period.

    Creates a new period row if one doesn't exist (30-day rolling window).
    """
    # Ensure a current period exists.
    db.execute(
        "INSERT INTO token_usage (user_id, tokens_used, period_start, period_end) "
        "VALUES (%s, 0, NOW(), NOW() + INTERVAL '30 days') "
        "ON CONFLICT DO NOTHING",
        (user_id,),
    )
    # Increment usage.
    db.execute(
        "UPDATE token_usage SET tokens_used = tokens_used + %s "
        "WHERE user_id = %s AND period_start <= NOW() AND period_end > NOW()",
        (tokens, user_id),
    )
