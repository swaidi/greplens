"""App updates: at most one check per day on startup, from the public GitHub releases."""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from . import __version__
from .config import REPO, load_settings, save_settings


def _ver(text: str) -> tuple:
    return tuple(int(p) for p in text.lstrip("v").split("-")[0].split(".") if p.isdigit())


def check(force: bool = False) -> dict:
    settings = load_settings()
    today = dt.date.today().isoformat()
    if not force and (not settings.get("update_check") or settings.get("last_update_check") == today
                      or settings.get("remind_update_after", "") > today):
        return {"checked": False, "current": __version__}
    save_settings({"last_update_check": today})
    try:
        rel = httpx.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=8,
                        headers={"Accept": "application/vnd.github+json"}).json()
    except Exception as exc:
        return {"checked": False, "current": __version__, "error": str(exc)}
    latest = rel.get("tag_name", "")
    asset = next((a for a in rel.get("assets", []) if a["name"].lower().endswith(
        ("-setup.exe", ".msi") if sys.platform == "win32" else (".appimage", ".deb"))), None)
    return {"checked": True, "current": __version__, "latest": latest.lstrip("v"),
            "available": bool(latest) and _ver(latest) > _ver(__version__),
            "notes": rel.get("body", "")[:2000], "url": rel.get("html_url"),
            "asset": asset["browser_download_url"] if asset else None}


def remind_later() -> None:
    save_settings({"remind_update_after": (dt.date.today() + dt.timedelta(days=1)).isoformat()})


def install(asset_url: str) -> dict:
    """Download the installer and start it; the installer closes and replaces the running app."""
    target = Path(tempfile.gettempdir()) / asset_url.rsplit("/", 1)[-1]
    with httpx.stream("GET", asset_url, follow_redirects=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(target, "wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
    if sys.platform == "win32":
        os.startfile(str(target))  # noqa: S606 - user-approved installer
    else:
        subprocess.Popen(["xdg-open", str(target)])
    return {"started": True, "file": str(target)}
