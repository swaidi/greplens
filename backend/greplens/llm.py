"""Chat and embedding calls.

Chat goes to the local engine (OpenAI-compatible API), or to the cloud provider the workspace chose
(see providers.py). Embeddings always stay on the local engine.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Callable

import httpx

from . import engine, providers
from .config import FAKE_AI

THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def chat(role: str, messages: list[dict], *, max_tokens: int = 900, temperature: float = 0.2,
         json_mode: bool = False, fake: Callable[[], str] | None = None) -> str:
    provider = providers.current()
    if provider != providers.LOCAL:  # the workspace uses a cloud AI (Settings > AI Providers)
        text = providers.chat(provider, messages, max_tokens=max_tokens, temperature=temperature, json_mode=json_mode)
        return THINK_RE.sub("", text).strip()
    if FAKE_AI:
        return fake() if fake else "OK"
    base, model = engine.endpoint(role)
    messages = [dict(m) for m in messages]
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n/no_think"  # Qwen3: answer directly, no reasoning trace
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
            "chat_template_kwargs": {"enable_thinking": False}}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    resp = httpx.post(f"{base}/chat/completions", json=body, timeout=httpx.Timeout(900, connect=10))
    resp.raise_for_status()
    payload = resp.json()
    _record_speed(role, payload.get("timings", {}).get("predicted_per_second"))
    text = payload["choices"][0]["message"]["content"] or ""
    return THINK_RE.sub("", text).strip()


def _record_speed(role: str, tokens_per_second: float | None) -> None:
    """Keep a running measured speed per role, shown in Settings and the setup wizard."""
    if not tokens_per_second:
        return
    from .config import load_settings, save_settings
    speeds = load_settings().get("speeds", {})
    old = speeds.get(role)
    words = tokens_per_second * 0.75  # about 0.75 English words per token
    speeds[role] = round(words if not old else 0.7 * old + 0.3 * words, 1)
    save_settings({"speeds": speeds})


def parse_json(text: str, default):
    text = THINK_RE.sub("", text).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for candidate in (text, _outer(text, "{", "}"), _outer(text, "[", "]")):
        if candidate:
            try:
                return json.loads(candidate)
            except ValueError:
                continue
    return default


def _outer(text: str, open_ch: str, close_ch: str) -> str | None:
    start, end = text.find(open_ch), text.rfind(close_ch)
    return text[start:end + 1] if 0 <= start < end else None


def embed(texts: list[str]) -> list[list[float]]:
    if FAKE_AI:
        return [_fake_vector(t) for t in texts]
    base, model = engine.endpoint("embed")
    out: list[list[float]] = []
    for i in range(0, len(texts), 16):
        batch = [t[:6000] for t in texts[i:i + 16]]
        resp = httpx.post(f"{base}/embeddings", json={"model": model, "input": batch}, timeout=600)
        resp.raise_for_status()
        out += [item["embedding"] for item in sorted(resp.json()["data"], key=lambda d: d["index"])]
    return out


def _fake_vector(text: str, dims: int = 256) -> list[float]:
    vec = [0.0] * dims
    for word in re.findall(r"\w+", text.lower()):
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % dims] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]
