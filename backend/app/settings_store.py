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

# Server-side DeepSeek API key — injected into all users' providers so they
# can use the service without providing their own key. Set via the
# DEEPSEEK_API_KEY environment variable on Railway.
_DEEPSEEK_SERVER_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

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
    # Inject server-side DeepSeek key if the user hasn't set their own.
    if _DEEPSEEK_SERVER_KEY:
        for p in providers:
            if p["id"] == "deepseek" and not p.get("api_key"):
                p["api_key"] = _DEEPSEEK_SERVER_KEY
    return providers


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


def get_rollover_hour() -> int:
    raw = load_settings().get("day_rollover_hour", 0)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value in (0, 4) else 0


def get_vault_root() -> str:
    return load_settings().get("vault_root", DEFAULT_SETTINGS["vault_root"])
