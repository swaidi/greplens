"""Review categories (profiles). Built-in profiles ship with the app; user profiles stay on the device."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import data_dir

BUILTIN = Path(__file__).with_name("builtin.json")


def _user_dir() -> Path:
    return data_dir() / "profiles"


def list_profiles() -> list[dict]:
    profiles = {p["id"]: p for p in json.loads(BUILTIN.read_text(encoding="utf-8"))}
    for path in sorted(_user_dir().glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            item["builtin"] = False
            profiles[item["id"]] = item
        except (ValueError, KeyError):
            continue
    return list(profiles.values())


def get_many(ids: list[str]) -> list[dict]:
    known = {p["id"]: p for p in list_profiles()}
    chosen = [known[i] for i in ids if i in known][:2]
    return chosen or [known["general"]]


def save(profile: dict) -> dict:
    pid = profile.get("id") or re.sub(r"[^a-z0-9]+", "-", profile["name"].lower()).strip("-") or "custom"
    item = {"id": pid, "name": profile["name"], "name_ar": profile.get("name_ar", profile["name"]),
            "persona": profile.get("persona") or f"a {profile['name'].lower()} reviewer",
            "focus": [f.strip() for f in profile.get("focus", []) if f.strip()]}
    (_user_dir() / f"{pid}.json").write_text(json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8")
    return item


def delete(pid: str) -> None:
    path = _user_dir() / f"{pid}.json"
    if path.exists():
        path.unlink()
