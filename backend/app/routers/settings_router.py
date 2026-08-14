"""User settings routes: LLM providers, API keys, and model routing.

Settings are stored per-user in the ``user_settings`` table as a JSON blob.
The ``get_current_user`` dependency already binds the loaded settings into the
request contextvar, so these routes read/write through ``settings_store``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import auth, settings_store

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ProvidersUpdate(BaseModel):
    providers: list[dict] | None = None
    default_model: str | None = None
    writer_model: str | None = None
    critic_model: str | None = None
    planner_model: str | None = None
    audiobook_model: str | None = None
    model_routing: dict | None = None


@router.get("/providers")
async def get_providers(current=Depends(auth.get_current_user)):
    """Return the user's configured providers + model role assignments.

    Each provider includes a key_source field: "user" (user's own key),
    "openwrite" (platform-provided key), or "none" (no key set).
    """
    settings = settings_store.load_settings()
    return {
        "providers": settings_store.get_providers(),
        "default_model": settings.get("default_model", ""),
        "writer_model": settings.get("writer_model", ""),
        "critic_model": settings.get("critic_model", ""),
        "planner_model": settings.get("planner_model", ""),
        "audiobook_model": settings.get("audiobook_model", ""),
        "model_routing": settings.get("model_routing", {}),
        "server_key_providers": settings_store.get_server_key_providers(),
    }


@router.put("/providers")
async def update_providers(req: ProvidersUpdate, current=Depends(auth.get_current_user)):
    settings = settings_store.load_settings()
    if req.providers is not None:
        settings["providers"] = settings_store._normalize_providers(req.providers)
    if req.default_model is not None:
        settings["default_model"] = req.default_model
    if req.writer_model is not None:
        settings["writer_model"] = req.writer_model
    if req.critic_model is not None:
        settings["critic_model"] = req.critic_model
    if req.planner_model is not None:
        settings["planner_model"] = req.planner_model
    if req.audiobook_model is not None:
        settings["audiobook_model"] = req.audiobook_model
    if req.model_routing is not None:
        settings["model_routing"] = req.model_routing
    settings_store.save_settings(settings)
    return {
        "providers": settings_store.get_providers(),
        "default_model": settings.get("default_model", ""),
        "writer_model": settings.get("writer_model", ""),
        "critic_model": settings.get("critic_model", ""),
        "planner_model": settings.get("planner_model", ""),
        "audiobook_model": settings.get("audiobook_model", ""),
        "model_routing": settings.get("model_routing", {}),
    }


class TestConnectionRequest(BaseModel):
    provider_id: str


@router.post("/test-connection")
async def test_connection(req: TestConnectionRequest, current=Depends(auth.get_current_user)):
    """Verify an API key by hitting the provider's /models endpoint."""
    from app.ai.openrouter import test_connection as _test
    providers = {p["id"]: p for p in settings_store.get_providers()}
    p = providers.get(req.provider_id)
    if not p:
        return {"ok": False, "error": "Unknown provider."}
    if not p.get("api_key"):
        return {"ok": False, "error": "No API key set for this provider."}
    return await _test(p["api_key"], base_url=p.get("base_url") or "https://openrouter.ai/api/v1")


@router.get("/models/{provider_id}")
async def get_provider_models(provider_id: str, current=Depends(auth.get_current_user)):
    """Fetch the live model list from a provider's /models endpoint.

    Falls back to the provider's curated seed list if the key is absent
    or if the provider does not implement /models.

    Returns:
        {
          "models": [{"id": "provider_id/model_id", "name": "human-readable name"}, ...],
          "source": "live" | "curated"
        }
    """
    from app.ai.openrouter import list_models as _list_models

    providers = {p["id"]: p for p in settings_store.get_providers()}
    p = providers.get(provider_id)
    if not p:
        return {"models": [], "source": "none", "error": "Unknown provider."}

    curated = [
        {"id": f"{provider_id}/{m}", "name": m}
        for m in p.get("models", [])
    ]

    if not p.get("api_key"):
        return {"models": curated, "source": "curated"}

    try:
        live = await _list_models(
            p["api_key"],
            base_url=p.get("base_url") or "https://openrouter.ai/api/v1",
        )
        # live entries have "id" (bare model id from the provider) and "name"
        return {
            "models": [
                {"id": f"{provider_id}/{m['id']}", "name": m.get("name") or m["id"]}
                for m in live
            ],
            "source": "live",
        }
    except Exception:
        # Provider doesn't expose /models or key isn't valid for that endpoint —
        # return the curated list so the UI still has options.
        return {"models": curated, "source": "curated"}


@router.get("/token-usage")
async def get_token_usage(current=Depends(auth.get_current_user)):
    """Return the user's current token usage, allowance, and reset date."""
    usage = settings_store.get_token_usage(current["id"])
    tier = settings_store.get_user_tier(current["id"])
    return {
        **usage,
        "tier": tier,
        "allowed_models": settings_store.get_allowed_models(current["id"]),
    }


@router.get("/account-tier")
async def get_account_tier(current=Depends(auth.get_current_user)):
    """Return the user's account tier and its properties."""
    tier = settings_store.get_user_tier(current["id"])
    return {
        "tier": tier,
        "allowed_models": settings_store.get_allowed_models(current["id"]),
        "monthly_tokens": settings_store.get_monthly_token_allowance(current["id"]),
    }
