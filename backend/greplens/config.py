"""Paths and user settings.

All data lives in one organized folder:
  <data>/greplens.db        SQLite database (workspaces, documents, notes, findings, chats)
  <data>/documents/<id>/    original uploads and OCR copies
  <data>/index/             Chroma vector store
  <data>/profiles/          user-defined review categories (never published)
  <data>/settings.json      user settings
Models are stored in a separate folder the user chooses in the setup wizard.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

APP_NAME = "GrepLens"
API_PORT = int(os.environ.get("GREPLENS_PORT", "8710"))
REPO = "swaidi/greplens"
FAKE_AI = os.environ.get("GREPLENS_FAKE_AI") == "1"  # deterministic stand-in for tests and UI work


def data_dir() -> Path:
    if os.environ.get("GREPLENS_DATA"):
        base = Path(os.environ["GREPLENS_DATA"])
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "greplens"
    for sub in ("documents", "index", "profiles", "packages", "logs"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


def app_dir() -> Path:
    """Folder holding bundled resources (llama-server engine, tessdata, static UI)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def resources_dir() -> Path:
    """Folder holding the AI engine and OCR language data (set by the desktop shell)."""
    return Path(os.environ["GREPLENS_RESOURCES"]) if os.environ.get("GREPLENS_RESOURCES") else app_dir()


DEFAULTS = {
    "setup_complete": False,
    "language": "en",
    "theme": "system",            # system | light | dark
    "pen_mode": "draw",           # what a pen does on a PDF page: draw | highlight | select
    "view": "split",              # split | margin | page: where notes are shown beside the document
    "models_dir": "",
    "engine": "builtin",          # builtin (bundled llama.cpp) | ollama
    "models": {},                 # role -> {"path": ..., "id": ..., "source": ...}
    "threads": 0,                 # 0 = physical cores
    "update_check": True,
    "last_update_check": "",
    "remind_update_after": "",
    "author": "You",
}

_lock = threading.Lock()


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    with _lock:
        try:
            stored = json.loads(settings_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
    merged = dict(DEFAULTS)
    merged.update(stored)
    return merged


def save_settings(changes: dict) -> dict:
    current = load_settings()
    current.update(changes)
    with _lock:
        tmp = settings_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, settings_path())
    return current
