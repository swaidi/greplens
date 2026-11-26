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
            _run_installer_after_exit(target)
        else:
            subprocess.Popen(["xdg-open", str(target)])
        _install["status"] = "started"
    except Exception as exc:
        _log(f"install failed: {exc!r}")
        _install.update(status="error", message=f"The update could not be downloaded: {exc}")


def _run_installer_after_exit(installer: Path) -> None:
    """Close GrepLens completely, then run the installer as an update, and reopen GrepLens.

    The installer must not be a child of GrepLens: closing GrepLens ends its whole process tree, which
    would also end an installer started from it. A separate PowerShell helper, outside that tree, closes
    every GrepLens process running from the install folder, waits until they are gone, then runs the
    installer in update mode (/UPDATE: no "uninstall first" page, no running-app prompt; /P: progress
    bar only; /R: open GrepLens again when done).
    """
    folder = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
    q = lambda p: str(p).replace("'", "''")  # noqa: E731 - PowerShell single-quoted string
    log_folder = data_dir() / "logs"
    script = [
        f"Start-Transcript -Path '{q(log_folder / 'update-helper.log')}' -Force | Out-Null",
        "$ErrorActionPreference = 'Continue'",
        "Start-Sleep -Milliseconds 800",
    ]
    if folder:
        script += [
            f"$dir = '{q(folder)}\\'",
            "$procs = { Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($dir, 'OrdinalIgnoreCase') } }",
            "& $procs | Where-Object { $_.ProcessName -notmatch 'greplens-backend|llama-server' } | Stop-Process -Force",
            "& $procs | Stop-Process -Force",
            "for ($i = 0; $i -lt 40 -and (& $procs); $i++) { Start-Sleep -Milliseconds 250; & $procs | Stop-Process -Force }",
        ]
    script += [
        "'GrepLens processes left: ' + @(& $procs).Count" if folder else "'no install folder'",
        f"Start-Process -FilePath '{q(installer)}' -ArgumentList '/UPDATE','/P','/R'",
        "'installer started'",
        "Stop-Transcript | Out-Null",
    ]
    (log_folder / "update-helper.ps1").write_text("\n".join(script), encoding="utf-8")
    flags = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW (its own hidden console)
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File",
            str(log_folder / "update-helper.ps1")]
    out = open(log_folder / "update-helper.out", "w", encoding="utf-8")  # noqa: SIM115 - kept open for the child
    kw = dict(stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, close_fds=True)
    try:  # also leave any job object GrepLens belongs to, so closing GrepLens cannot end the helper
        subprocess.Popen(args, creationflags=flags | 0x01000000, **kw)  # CREATE_BREAKAWAY_FROM_JOB
    except OSError as exc:
        _log(f"install: helper without job breakaway ({exc})")
        subprocess.Popen(args, creationflags=flags, **kw)
    _log(f"install: helper started for {installer} (folder {folder})")
