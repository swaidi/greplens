"""Online AI services: Claude, ChatGPT (OpenAI), Gemini and OpenRouter, used with the user's own key.

Only answering, reviewing, translating and the document tools go online; document search (embeddings)
always stays on this computer. A workspace marked "Keep on this computer" never goes online.
"""
from __future__ import annotations

import contextvars
import re
import time
from datetime import datetime

import httpx

from . import keystore
from .config import FAKE_AI, load_settings, save_settings

PROVIDERS = {
    "anthropic": {
        "name": "Claude", "company": "Anthropic", "key_url": "https://platform.claude.com/settings/keys",
        "billing_url": "https://platform.claude.com/settings/billing", "base": "https://api.anthropic.com/v1",
        "steps": ["signIn", "addCredit", "createKey"],
    },
    "openai": {
        "name": "ChatGPT", "company": "OpenAI", "key_url": "https://platform.openai.com/api-keys",
        "billing_url": "https://platform.openai.com/settings/organization/billing/overview", "base": "https://api.openai.com/v1",
        "steps": ["signIn", "addCredit", "createKey"],
    },
    "gemini": {
        "name": "Gemini", "company": "Google", "key_url": "https://aistudio.google.com/apikey",
        "billing_url": "https://aistudio.google.com/usage", "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "steps": ["signInGoogle", "createKey", "copyKey"], "free_tier": True,
    },
    "openrouter": {
        "name": "OpenRouter", "company": "OpenRouter", "key_url": "https://openrouter.ai/keys",
        "billing_url": "https://openrouter.ai/credits", "base": "https://openrouter.ai/api/v1",
        "steps": ["signIn", "addCredit", "createKey"],
    },
}
ALLOWED_URLS = {u for p in PROVIDERS.values() for u in (p["key_url"], p["billing_url"])}

# rough prices in US dollars per million tokens (input, output), by model family; shown as "about"
PRICES = [
    (r"claude.*haiku", (1.0, 5.0)), (r"claude.*sonnet", (2.0, 10.0)), (r"claude.*opus", (5.0, 25.0)),
    (r"gpt.*nano", (0.05, 0.4)), (r"gpt.*mini", (0.25, 2.0)), (r"gpt", (1.25, 10.0)),
    (r"gemini.*flash.*lite", (0.1, 0.4)), (r"gemini.*flash", (0.3, 2.5)), (r"gemini.*pro", (1.25, 10.0)),
]

_workspace: contextvars.ContextVar[str | None] = contextvars.ContextVar("greplens_workspace", default=None)
last_error: dict = {}


class OnlineError(RuntimeError):
    """A problem with the online service, in words a non-technical user understands."""


def set_workspace(workspace_id: str | None) -> None:
    _workspace.set(workspace_id)


def detect(key: str) -> str | None:
    """Which service a key belongs to, from how it starts."""
    key = key.strip()
    if key.startswith("sk-ant-"):
        return "anthropic"
    if key.startswith("sk-or-"):
        return "openrouter"
    if key.startswith("AIza"):
        return "gemini"
    if key.startswith("sk-"):
        return "openai"
    return None


def config() -> dict:
    return load_settings().get("online") or {}


def status() -> dict:
    cfg = config()
    provider = cfg.get("provider")
    connected = bool(provider and keystore.get(provider))
    return {
        "source": load_settings().get("ai_source", "local"),
        "provider": provider if connected else None,
        "provider_name": PROVIDERS[provider]["name"] if connected else None,
        "models": cfg.get("models", {}) if connected else {},
        "available_models": cfg.get("available", []) if connected else [],
        "key_hint": _hint(keystore.get(provider)) if connected else "",
        "usage": usage(),
        "last_error": last_error if last_error and time.time() - last_error.get("at", 0) < 600 else None,
        "providers": {k: {kk: v[kk] for kk in ("name", "company", "key_url", "steps")} | {"free_tier": v.get("free_tier", False)}
                      for k, v in PROVIDERS.items()},
    }


def _hint(key: str | None) -> str:
    return f"{key[:7]}…{key[-4:]}" if key and len(key) > 14 else ""


def target(role: str) -> dict | None:
    """The online service to use for this role, or None to use this computer."""
    if role == "embed" or load_settings().get("ai_source") != "online":
        return None
    ws = _workspace.get()
    if ws:
        from . import db
        row = db.get("workspaces", ws)
        if row and row.get("local_only"):
            return None
    cfg = config()
    provider = cfg.get("provider")
    key = keystore.get(provider) if provider else None
    if not key:
        return None
    models = cfg.get("models", {})
    model = models.get(role) or models.get("fast")
    return {"provider": provider, "key": key, "model": model} if model else None


# ---------------------------------------------------------------- connecting a key

def connect(key: str, provider: str | None = None) -> dict:
    """Check a key, choose good models for each job, and save the key securely."""
    key = re.sub(r"\s+", "", key or "")
    provider = provider or detect(key)
    if not key:
        raise OnlineError("Paste your key first.")
    if provider not in PROVIDERS:
        raise OnlineError("This doesn't look like a key from Claude, ChatGPT, Gemini or OpenRouter. "
                          "Copy the whole key again from the service's key page.")
    if detect(key) and detect(key) != provider and not (provider == "openai" and detect(key) == "openai"):
        raise OnlineError(f"This key is for {PROVIDERS[detect(key)]['name']}, not {PROVIDERS[provider]['name']}. "
                          "Choose that service, or paste the other key.")
    if FAKE_AI:
        available = ["fake-fast", "fake-review"]
        models = {"fast": "fake-fast", "review": "fake-review"}
    else:
        available = _list_models(provider, key)
        models = _choose(provider, available)
        _try_chat(provider, key, models["fast"])
    keystore.put(provider, key)
    save_settings({"online": {"provider": provider, "models": models, "available": available[:200]}, "ai_source": "online"})
    last_error.clear()
    return status()


def disconnect() -> dict:
    cfg = config()
    if cfg.get("provider"):
        keystore.delete(cfg["provider"])
    save_settings({"online": {}, "ai_source": "local"})
    return status()


def set_models(models: dict) -> dict:
    cfg = config()
    cfg["models"] = {**cfg.get("models", {}), **{k: v for k, v in models.items() if k in ("fast", "review") and v}}
    save_settings({"online": cfg})
    return status()


def _headers(provider: str, key: str) -> dict:
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if provider == "openrouter":
        headers |= {"HTTP-Referer": "https://github.com/swaidi/greplens", "X-Title": "GrepLens"}
    return headers


def _list_models(provider: str, key: str) -> list[str]:
    base = PROVIDERS[provider]["base"]
    url = f"{base}/models" + ("?limit=1000" if provider == "anthropic" else "")
    try:
        resp = httpx.get(url, headers=_headers(provider, key), timeout=30)
    except httpx.HTTPError as exc:
        raise OnlineError(_network_message(exc)) from exc
    if provider == "openrouter":  # the model list is public; check the key itself
        try:
            check = httpx.get(f"{base}/key", headers=_headers(provider, key), timeout=30)
        except httpx.HTTPError as exc:
            raise OnlineError(_network_message(exc)) from exc
        if check.status_code != 200:
            raise OnlineError(_status_message(provider, check))
    if resp.status_code != 200:
        raise OnlineError(_status_message(provider, resp))
    ids = [str(m.get("id", "")).removeprefix("models/") for m in resp.json().get("data", [])]
    return [i for i in ids if i]


def _version(model_id: str) -> tuple:
    nums = [int(n) for n in re.findall(r"(?<![\d])(\d{1,3})(?![\d])", model_id)[:4]]
    return tuple(nums) + (0,) * (4 - len(nums))


def _pick(ids: list[str], include: str, exclude: str = r"$^") -> str | None:
    found = [i for i in ids if re.search(include, i, re.I) and not re.search(exclude, i, re.I)]
    # newest version first; a stable name before a preview of the same version
    found.sort(key=lambda i: (_version(i), "preview" not in i and "exp" not in i, -len(i)), reverse=True)
    return found[0] if found else None


SKIP = r"audio|realtime|image|tts|transcribe|search|embedding|moderation|vision|live|computer|codex|instruct|dall|whisper|thinking|deep-research|oss"


def _choose(provider: str, ids: list[str]) -> dict:
    if provider == "anthropic":
        fast = _pick(ids, r"haiku") or _pick(ids, r"sonnet")
        review = _pick(ids, r"sonnet") or fast
    elif provider == "openai":
        fast = _pick(ids, r"^gpt-\d.*-mini$|^gpt-\d.*-mini-\d{4}", SKIP) or _pick(ids, r"^gpt-\d", SKIP + r"|nano")
        review = _pick(ids, r"^gpt-\d+(\.\d+)?$", SKIP) or _pick(ids, r"^gpt-\d", SKIP + r"|mini|nano|pro") or fast
    elif provider == "gemini":
        fast = _pick(ids, r"gemini.*flash", SKIP + r"|lite") or _pick(ids, r"gemini.*flash", SKIP)
        review = _pick(ids, r"gemini.*pro", SKIP) or fast
    else:  # OpenRouter: one key for many services
        fast = _pick(ids, r"^anthropic/claude.*haiku") or _pick(ids, r"^google/gemini.*flash", SKIP + r"|lite") \
            or _pick(ids, r"^openai/gpt-\d.*mini", SKIP)
        review = _pick(ids, r"^anthropic/claude.*sonnet") or _pick(ids, r"^google/gemini.*pro", SKIP) or fast
    if not fast:
        raise OnlineError("This key works, but no suitable model is available on it. Check the account on the service's website.")
    return {"fast": fast, "review": review or fast}


def _try_chat(provider: str, key: str, model: str) -> None:
    """A tiny request, so a key without credit is found now rather than in the middle of a review."""
    _send(provider, key, model, [{"role": "user", "content": "Reply with the word OK."}], max_tokens=20, temperature=0)


# ---------------------------------------------------------------- calling the service

def chat(t: dict, role: str, messages: list[dict], *, max_tokens: int, temperature: float, json_mode: bool) -> str:
    try:
        text, usage_in, usage_out = _send(t["provider"], t["key"], t["model"], messages, max_tokens=max_tokens,
                                          temperature=temperature, json_mode=json_mode)
    except OnlineError as exc:
        _remember_error(str(exc))
        raise
    _record(t["model"], usage_in, usage_out)
    last_error.clear()
    return text


def _send(provider: str, key: str, model: str, messages: list[dict], *, max_tokens: int, temperature: float,
          json_mode: bool = False) -> tuple[str, int, int]:
    base = PROVIDERS[provider]["base"]
    headers = _headers(provider, key)
    if provider == "anthropic":
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        body = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
                "messages": [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]}
        if system:
            body["system"] = system + ("\nReply with JSON only." if json_mode else "")
        resp = _post(provider, f"{base}/messages", headers, body)
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        u = data.get("usage", {})
        return text.strip(), u.get("input_tokens", 0), u.get("output_tokens", 0)
    # OpenAI-style services. Newer models think before answering and only accept their own settings,
    # so the request is adjusted and retried when the service says a setting isn't supported.
    body: dict = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if json_mode and provider != "gemini":
        body["response_format"] = {"type": "json_object"}
    if provider == "openai" and re.match(r"(gpt-5|o\d)", model):
        body.pop("max_tokens"); body.pop("temperature")
        body["max_completion_tokens"] = max_tokens + 4000  # room for the model's own thinking
        body["reasoning_effort"] = "low"
    if provider == "gemini" and re.search(r"gemini-(2\.5|[3-9])", model):
        body["reasoning_effort"] = "low"
        body["max_tokens"] = max_tokens + 4000
    for _ in range(3):
        try:
            resp = _post(provider, f"{base}/chat/completions", headers, body)
            break
        except _Unsupported as exc:
            if "temperature" in exc.text and "temperature" in body:
                body.pop("temperature")
            elif "max_tokens" in exc.text and "max_tokens" in body:
                body["max_completion_tokens"] = body.pop("max_tokens") + 4000
            elif "reasoning_effort" in exc.text and "reasoning_effort" in body:
                body.pop("reasoning_effort")
            elif "response_format" in exc.text and "response_format" in body:
                body.pop("response_format")
            else:
                raise OnlineError(_friendly(provider, 400, exc.text)) from None
    else:
        raise OnlineError("The online service did not accept the request.")
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    u = data.get("usage") or {}
    return text.strip(), u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


class _Unsupported(Exception):
    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


def _post(provider: str, url: str, headers: dict, body: dict) -> httpx.Response:
    for attempt in range(3):
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=httpx.Timeout(300, connect=15))
        except httpx.HTTPError as exc:
            if attempt == 2:
                raise OnlineError(_network_message(exc)) from exc
            time.sleep(2 * (attempt + 1))
            continue
        if resp.status_code == 200:
            return resp
        if resp.status_code == 400 and re.search(r"unsupported|not supported|does not support|unrecognized|unknown|invalid.*(param|argument)",
                                                 resp.text, re.I):
            raise _Unsupported(resp.text)
        if resp.status_code in (429, 500, 502, 503, 504, 529) and attempt < 2 and not _no_credit(resp.text):
            time.sleep(int(resp.headers.get("retry-after", "0") or 0) or 3 * (attempt + 1))
            continue
        raise OnlineError(_status_message(provider, resp))
    raise OnlineError("The online service is busy. Try again in a minute.")


def _no_credit(text: str) -> bool:
    return bool(re.search(r"credit|quota|billing|balance|insufficient|payment", text, re.I))


def _status_message(provider: str, resp: httpx.Response) -> str:
    return _friendly(provider, resp.status_code, resp.text)


def _friendly(provider: str, code: int, text: str) -> str:
    name = PROVIDERS[provider]["name"]
    if code in (401, 403) and not _no_credit(text):
        return (f"{name} did not accept this key. It may have been copied incompletely, or deleted. "
                "Copy the whole key again from the key page.")
    if code == 402 or _no_credit(text):
        return f"This {name} account has no credit left. Add credit on the {name} billing page, then try again."
    if code == 429:
        return f"{name} is receiving too many requests from this key. Wait a minute and try again."
    if code == 404:
        return f"The model chosen for {name} is no longer available. Open Settings > Online AI and connect again."
    if code >= 500:
        return f"{name} is having problems right now. Try again in a few minutes."
    detail = re.sub(r"\s+", " ", text)[:200]
    return f"{name} could not complete the request ({code}). {detail}"


def _network_message(exc: Exception) -> str:
    return "GrepLens can't reach the online service. Check the internet connection and try again."


def _remember_error(message: str) -> None:
    from . import engine
    local = bool(load_settings().get("models", {}).get("fast")) and bool(engine.server_binary())
    last_error.update(message=message, at=time.time(), can_use_local=local)


# ---------------------------------------------------------------- usage this month

def _price(model: str) -> tuple[float, float] | None:
    for pattern, price in PRICES:
        if re.search(pattern, model, re.I):
            return price
    return None


def _record(model: str, tokens_in: int, tokens_out: int) -> None:
    month = datetime.now().strftime("%Y-%m")
    all_usage = load_settings().get("ai_usage", {})
    u = all_usage.get(month, {"input": 0, "output": 0, "cost": 0.0, "requests": 0, "unpriced": 0})
    price = _price(model)
    u["input"] += tokens_in
    u["output"] += tokens_out
    u["requests"] += 1
    if price:
        u["cost"] = round(u["cost"] + (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000, 4)
    else:
        u["unpriced"] = u.get("unpriced", 0) + 1
    all_usage = {k: v for k, v in all_usage.items() if k >= (datetime.now().replace(day=1).strftime("%Y-%m"))[:4]}
    all_usage[month] = u
    save_settings({"ai_usage": all_usage})


def usage() -> dict:
    month = datetime.now().strftime("%Y-%m")
    return load_settings().get("ai_usage", {}).get(month, {"input": 0, "output": 0, "cost": 0.0, "requests": 0, "unpriced": 0})
