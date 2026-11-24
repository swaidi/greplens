"""App updates from the public GitHub releases.

- check(): at most once a day on startup (or on demand from Settings > Updates > Check now)
- start_install(): downloads the installer in the background with progress, stops the AI engine,
  then starts the installer, which closes GrepLens and replaces it.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

from . import __version__
from .config import REPO, data_dir, load_settings, save_settings

HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": f"GrepLens/{__version__}"}
_install: dict = {"status": "idle", "done": 0, "total": 0, "message": ""}


def _log(message: str) -> None:
    with open(data_dir() / "logs" / "updates.log", "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")


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
        resp = httpx.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=15, headers=HEADERS,
                         follow_redirects=True)
        if resp.status_code == 404:
            _log("check: no published release yet")
            return {"checked": True, "current": __version__, "available": False}
        resp.raise_for_status()
        rel = resp.json()
    except Exception as exc:
        _log(f"check failed: {exc!r}")
        return {"checked": False, "current": __version__,
                "error": f"Could not reach GitHub to check for updates ({exc.__class__.__name__})."}
    latest = rel.get("tag_name", "")
    suffixes = ("-setup.exe", ".msi") if sys.platform == "win32" else (".appimage", ".deb")
    asset = next((a for a in rel.get("assets", []) if a["name"].lower().endswith(suffixes)), None)
    result = {"checked": True, "current": __version__, "latest": latest.lstrip("v"),
              "available": bool(latest) and _ver(latest) > _ver(__version__),
              "notes": (rel.get("body") or "")[:2000], "url": rel.get("html_url"),
              "asset": asset["browser_download_url"] if asset else None,
              "size": asset["size"] if asset else 0}
    _log(f"check: current {__version__}, latest {latest}, available {result['available']}, asset {bool(asset)}")
    return result


def remind_later() -> None:
    save_settings({"remind_update_after": (dt.date.today() + dt.timedelta(days=1)).isoformat()})


def progress() -> dict:
    return dict(_install)


def start_install(asset_url: str) -> dict:
    if _install["status"] in ("downloading", "starting"):
        return progress()
    _install.update(status="downloading", done=0, total=0, message="Downloading the update")
    threading.Thread(target=_download_and_run, args=(asset_url,), daemon=True).start()
    return progress()


def _download_and_run(asset_url: str) -> None:
    target = Path(tempfile.gettempdir()) / asset_url.rsplit("/", 1)[-1]
    try:
        _log(f"install: downloading {asset_url}")
        with httpx.stream("GET", asset_url, follow_redirects=True, headers={"User-Agent": HEADERS["User-Agent"]},
                          timeout=httpx.Timeout(60, connect=20)) as resp:
            resp.raise_for_status()
            _install["total"] = int(resp.headers.get("content-length") or 0)
            with open(target, "wb") as fh:
                for chunk in resp.iter_bytes(256 * 1024):
                    fh.write(chunk)
                    _install["done"] += len(chunk)
        _install.update(status="starting", message="Starting the installer. GrepLens will close and restart.")
        from . import engine
        engine.stop()  # release the engine files before the installer replaces them
        _log(f"install: starting {target}")
        if sys.platform == "win32":
            os.startfile(str(target))  # noqa: S606 - installer the user chose to run
        else:
            subprocess.Popen(["xdg-open", str(target)])
        _install["status"] = "started"
    except Exception as exc:
        _log(f"install failed: {exc!r}")
        _install.update(status="error", message=f"The update could not be downloaded: {exc}")
