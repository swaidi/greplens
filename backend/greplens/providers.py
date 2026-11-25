"""Optional cloud AI providers (Claude, OpenAI, Gemini) through LiteLLM.

- Each workspace chooses its AI: "local" (default, nothing leaves the computer) or a cloud provider.
- API keys are kept in the operating system's secure store (Windows Credential Manager, or the
  Secret Service keyring on Linux) through the keyring library. They are never written to the
  settings file, the database, logs or Workspace packages.
- Only chat calls go to the cloud. Search embeddings always stay local (BGE-M3).
- With Claude, the long, repeated instructions are marked for prompt caching to lower the cost.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager

from .config import load_settings, save_settings
from .engine import EngineError

SERVICE = "GrepLens"
LOCAL = "local"

# Default models are suggestions only; each can be changed in Settings > AI Providers.
PROVIDERS = {
    "claude": {"name": "Claude (Anthropic)", "prefix": "anthropic/", "default_model": "claude-sonnet-5-5",
               "key_hint": "sk-ant-…", "cache": True, "json_mode": False},
    "openai": {"name": "OpenAI", "prefix": "openai/", "default_model": "gpt-4.1",
               "key_hint": "sk-…", "cache": False, "json_mode": True},
    "gemini": {"name": "Gemini (Google)", "prefix": "gemini/", "default_model": "gemini-2.5-flash",
               "key_hint": "AIza…", "cache": False, "json_mode": True},
}

_current: contextvars.ContextVar[str] = contextvars.ContextVar("greplens_provider", default=LOCAL)


class ProviderError(EngineError):
    """Shown to the user like an engine error (HTTP 503 with a readable message)."""


# ------------------------------------------------------------------ keys (secure store only)
def _keyring():
    try:
        import keyring
        return keyring
    except ImportError as exc:  # pragma: no cover - keyring is a declared dependency
        raise ProviderError("The secure key store is not available on this computer.") from exc


def set_key(provider: str, key: str) -> None:
    _check(provider)
    try:
        _keyring().set_password(SERVICE, provider, key.strip())
    except Exception as exc:
        raise ProviderError(f"The key could not be saved in the system's secure store: {exc}") from exc


def get_key(provider: str) -> str | None:
    try:
        return _keyring().get_password(SERVICE, provider)
    except Exception:
        return None


def delete_key(provider: str) -> None:
    _check(provider)
    try:
        _keyring().delete_password(SERVICE, provider)
    except Exception:
        pass  # nothing stored


# ------------------------------------------------------------------ settings
def _check(provider: str) -> None:
    if provider not in PROVIDERS:
        raise KeyError(f"Unknown AI provider: {provider}")


def model_of(provider: str) -> str:
    stored = load_settings().get("providers", {}).get(provider, {})
    return stored.get("model") or PROVIDERS[provider]["default_model"]


def set_model(provider: str, model: str) -> None:
    _check(provider)
    current = load_settings().get("providers", {})
    current[provider] = {**current.get(provider, {}), "model": model.strip()}
    save_settings({"providers": current})


def listing() -> list[dict]:
    return [{"id": pid, "name": p["name"], "model": model_of(pid), "default_model": p["default_model"],
             "key_hint": p["key_hint"], "has_key": bool(get_key(pid)), "caching": p["cache"]}
            for pid, p in PROVIDERS.items()]


def available() -> list[str]:
    """Providers that can be chosen for a workspace (a key is stored)."""
    return [pid for pid in PROVIDERS if get_key(pid)]


# ------------------------------------------------------------------ which AI answers
@contextmanager
def use_workspace(workspace_id: str | None):
    """Route the chat calls made inside this block to the workspace's chosen AI."""
    provider = LOCAL
    if workspace_id:
        from . import db
        ws = db.get("workspaces", workspace_id)
        provider = (ws or {}).get("provider") or LOCAL
    token = _current.set(provider)
    try:
        yield provider
    finally:
        _current.reset(token)


def use_document(document_id: str | None):
    from . import db
    doc = db.get("documents", document_id) if document_id else None
    return use_workspace(doc["workspace_id"] if doc else None)


def current() -> str:
    return _current.get()


def chat(provider: str, messages: list[dict], *, max_tokens: int, temperature: float, json_mode: bool) -> str:
    _check(provider)
    key = get_key(provider)
    if not key:
        raise ProviderError(f"No API key is saved for {PROVIDERS[provider]['name']}. "
                            "Open Settings > AI Providers, or switch this workspace to Local AI.")
    spec = PROVIDERS[provider]
    messages = [dict(m) for m in messages]
    if spec["cache"]:
        messages = _mark_cache(messages)
    kwargs = {"model": spec["prefix"] + model_of(provider), "messages": messages, "api_key": key,
              "max_tokens": max_tokens, "temperature": temperature, "timeout": 300, "num_retries": 2}
    if json_mode and spec["json_mode"]:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        import litellm
        litellm.telemetry = False
        litellm.drop_params = True  # ignore settings a model does not support instead of failing
        resp = litellm.completion(**kwargs)
    except Exception as exc:
        raise ProviderError(f"{spec['name']} did not answer: {_short(exc)}") from exc
    return resp.choices[0].message.content or ""


def _mark_cache(messages: list[dict]) -> list[dict]:
    """Claude prompt caching: mark the system instructions (the long, repeated part) as cacheable."""
    out = []
    for i, m in enumerate(messages):
        if m["role"] == "system" and isinstance(m["content"], str) and i == 0:
            m = {**m, "content": [{"type": "text", "text": m["content"], "cache_control": {"type": "ephemeral"}}]}
        out.append(m)
    return out


def _short(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return text[:300]


def test(provider: str) -> dict:
    raw = chat(provider, [{"role": "user", "content": "Reply with the single word: OK"}],
               max_tokens=10, temperature=0, json_mode=False)
    return {"ok": True, "reply": raw.strip()[:40], "model": model_of(provider)}
