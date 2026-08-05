"""
providers.py — multi-provider model resolution.

Open-Write supports several OpenAI-compatible chat-completions providers
side by side: OpenRouter (the default), GLM (Zhipu), and MiMo, plus any custom
provider the writer adds in Settings. Each provider carries its own base_url,
API key, and model list.

Model identity
--------------
A model is referenced by a *qualified* id of the form ``"<provider>/<model>"``:

  - ``"glm/glm-4.6"``                -> GLM provider, model "glm-4.6"
  - ``"mimo/mimo-7b-instruct"``      -> MiMo provider
  - ``"openrouter/openai/gpt-4o-mini"`` -> OpenRouter (explicit)

Backward compatibility
----------------------
Unqualified ids that predate multi-provider support (e.g. ``"openai/gpt-4o-mini"``,
``"anthropic/claude-3.5-sonnet"``) are treated as OpenRouter models, since their
first segment is not a known provider id. This keeps existing settings working
without migration.

The resolved object exposes the three things an OpenAI-compatible client needs:
``base_url``, ``api_key``, and ``model_name`` (the bare model id to send to the
provider).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from app.settings_store import get_providers


@dataclass(frozen=True)
class ResolvedProvider:
    provider_id: str
    label: str
    base_url: str
    api_key: str
    model_name: str        # bare model id (no provider prefix)

    @property
    def is_configured(self) -> bool:
        """True if this provider has both a base_url and an api_key."""
        return bool(self.base_url) and bool(self.api_key)


def _extract_model_name(raw: str) -> str:
    """Normalize a model name that may be stored as a Python dict repr.

    The settings store sometimes contains ``"{'name': 'mimo-v2.5-pro', ...}"``
    instead of just ``"mimo-v2.5-pro"`` (a serialization bug in the model
    catalog picker). This extracts the actual model name so the API receives a
    valid model id.

    Also normalises common typing variants so the user doesn't have to get the
    exact string right:
      - ``mimo-v2.5-pro`` → ``mimo-2.5-pro``  (MiMo drops the leading 'v')
    """
    raw = raw.strip()
    if not raw.startswith("{"):
        return _normalise_model_name(raw)
    # Try ast.literal_eval (safe) first, then regex fallback.
    try:
        d = ast.literal_eval(raw)
        if isinstance(d, dict) and "name" in d:
            return _normalise_model_name(str(d["name"]))
    except (ValueError, SyntaxError):
        pass
    m = re.search(r"['\"]name['\"]\s*:\s*['\"]([^'\"]+)['\"]", raw)
    if m:
        return _normalise_model_name(m.group(1))
    return _normalise_model_name(raw)


def _normalise_model_name(name: str) -> str:
    """Return the model name unchanged.

    MiMo's API expects the ``v``-prefixed form (e.g. ``mimo-v2.5-pro``), so
    no normalisation is applied. Kept as a no-op in case it is called elsewhere.
    """
    return name


def _provider_index(providers: list[dict] | None = None) -> dict[str, dict]:
    src = providers if providers is not None else get_providers()
    return {p["id"]: p for p in src}


def resolve(model_id: str, providers: list[dict] | None = None) -> ResolvedProvider:
    """
    Resolve a (possibly qualified) model id to a provider + bare model name.

    Resolution order:
      1. If the id starts with a known provider id followed by '/', that
         provider is used and the remainder is the model name.
      2. For unqualified ids, check each provider's model list. Prefer
         configured providers (with API key) over unconfigured ones.
      3. If no provider owns the model, use the first configured provider
         and pass the model name through (the remote API will reject it
         if invalid, giving the user a clear error).
      4. Final fallback: OpenRouter (backward compatibility).
    """
    index = _provider_index(providers)
    model_id = (model_id or "").strip()

    provider = None
    model_name = model_id

    # Step 1: Qualified id (e.g. "mimo/mimo-v2.5-pro")
    if "/" in model_id:
        head, rest = model_id.split("/", 1)
        if head in index:
            provider = index[head]
            model_name = rest

    # Step 2: Unqualified id — scan provider model lists
    if provider is None and model_id:
        best_configured = None
        best_unconfigured = None
        for p in index.values():
            p_models = [_extract_model_name(m) for m in p.get("models", [])]
            if model_name in p_models:
                if p.get("api_key"):
                    best_configured = p
                    break
                elif best_unconfigured is None:
                    best_unconfigured = p
        provider = best_configured or best_unconfigured

    # Step 3: If the matched provider has no API key, try any configured provider
    if provider is not None and not provider.get("api_key"):
        for p in index.values():
            if p.get("api_key"):
                # Send the model name to a configured provider — the remote
                # API will return a clear "model not found" if it's wrong.
                provider = p
                break

    # Step 4: Fallback to OpenRouter
    if provider is None:
        provider = index.get("openrouter")
        if provider is None:
            raise ValueError(
                "No providers configured. Add a provider in Settings."
            )

    return ResolvedProvider(
        provider_id=provider["id"],
        label=provider.get("label", provider["id"]),
        base_url=provider.get("base_url", ""),
        api_key=provider.get("api_key", ""),
        model_name=_extract_model_name(model_name),
    )


def all_models() -> list[dict]:
    """
    Return every selectable model across all providers, for the model picker.

    Each entry: ``{"id": "<provider>/<model>", "label": "...", "provider": ...}``.
    OpenRouter contributes its (possibly scraped) model list; the other
    providers contribute their curated lists. OpenRouter's scraped models are
    loaded lazily by the settings/routes layer, so here we emit only the
    curated (non-OpenRouter) provider models plus an explicit entry per
    OpenRouter model already known to settings.
    """
    out: list[dict] = []
    for p in get_providers():
        for m in p.get("models", []):
            name = _extract_model_name(m)
            out.append({
                "id": f"{p['id']}/{name}",
                "label": f"{p.get('label', p['id'])} — {name}",
                "provider": p["id"],
                "model": name,
            })
    return out
