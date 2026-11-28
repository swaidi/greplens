"""Model manager: manifest, detection of models already on the device, resumable verified downloads.

The manifest can be updated from the public repository (models.json), so recommended models can change
without a new app release. File sizes and SHA-256 checksums come from Hugging Face's file metadata.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import httpx

from .config import REPO, load_settings, save_settings

MANIFEST = [
    {"id": "qwen3-8b", "role": "fast", "name": "Qwen3 8B", "use": "Questions, answers and translation",
     "repo": "bartowski/Qwen_Qwen3-8B-GGUF", "file": "*Q4_K_M.gguf", "size_gb": 5.0, "match": ["qwen3", "8b"],
     "ollama": ["qwen3:8b"]},
    {"id": "qwen3-14b", "role": "review", "name": "Qwen3 14B", "use": "Full document review",
     "repo": "bartowski/Qwen_Qwen3-14B-GGUF", "file": "*Q4_K_M.gguf", "size_gb": 9.0, "match": ["qwen3", "14b"],
     "ollama": ["qwen3:14b"]},
    {"id": "qwen3-4b", "role": "fast", "name": "Qwen3 4B", "use": "Questions and answers on devices with less memory",
     "repo": "bartowski/Qwen_Qwen3-4B-GGUF", "file": "*Q4_K_M.gguf", "size_gb": 2.5, "match": ["qwen3", "4b"],
     "ollama": ["qwen3:4b"]},
    {"id": "bge-m3", "role": "embed", "name": "BGE-M3", "use": "Document search in Arabic and English",
     "repo": "gpustack/bge-m3-GGUF", "file": "*Q8_0.gguf", "size_gb": 0.6, "match": ["bge-m3"], "ollama": ["bge-m3"]},
]
REMOTE_MANIFEST = f"https://raw.githubusercontent.com/{REPO}/main/models.json"
HF = "https://huggingface.co"

_state: dict[str, dict] = {}
_pause = threading.Event()
_thread: threading.Thread | None = None


def manifest() -> list[dict]:
    try:
        remote = httpx.get(REMOTE_MANIFEST, timeout=5).json()
        if isinstance(remote, list) and all("id" in m and "repo" in m for m in remote):
            return remote
    except Exception:
        pass
    return MANIFEST


def by_id(model_id: str) -> dict | None:
    return next((m for m in manifest() if m["id"] == model_id), None)


# ------------------------------------------------------------------ detection
def _ollama_root() -> Path:
    return Path(os.environ.get("OLLAMA_MODELS", Path.home() / ".ollama" / "models"))


def _ollama_models() -> list[dict]:
    root = _ollama_root()
    found = []
    for manifest_file in (root / "manifests").glob("*/*/*/*"):
        try:
            data = json.loads(manifest_file.read_text())
        except (OSError, ValueError):
            continue
        layer = next((l for l in data.get("layers", []) if l.get("mediaType") == "application/vnd.ollama.image.model"), None)
        if not layer:
            continue
        blob = root / "blobs" / layer["digest"].replace(":", "-")
        name = f"{manifest_file.parent.name}:{manifest_file.name}"
        if blob.exists():
            found.append({"path": str(blob), "label": name, "source": "Ollama", "ollama_name": name,
                          "size_gb": round(blob.stat().st_size / 1024 ** 3, 1)})
    return found


def _gguf_files() -> list[dict]:
    home = Path.home()
    roots = [home / ".lmstudio" / "models", home / ".cache" / "lm-studio" / "models",
             home / ".cache" / "huggingface" / "hub"]
    roots += model_folders()
    found, seen = [], set()
    for root in roots:
        if not root.exists():
            continue
        source = "LM Studio" if "lm" in str(root).lower() and "studio" in str(root).lower() else \
            "Hugging Face cache" if "huggingface" in str(root) else "Models folder"
        for path in root.rglob("*.gguf"):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if str(path).lower() in seen:
                continue
            seen.add(str(path).lower())
            found.append({"path": str(path), "label": path.name, "source": source, "size_gb": round(size / 1024 ** 3, 1)})
    return found


def default_folder() -> Path:
    """Where models go when the user hasn't chosen: D:\\GrepLens\\Models if there is a D drive, else C."""
    if load_settings().get("models_dir"):
        return Path(load_settings()["models_dir"])
    if sys.platform == "win32":
        return Path("D:/GrepLens/Models") if Path("D:/").exists() else Path("C:/GrepLens/Models")
    return Path.home() / "GrepLens" / "models"


def model_folders() -> list[Path]:
    """The chosen models folder, plus the usual GrepLens\\Models folder on every drive."""
    roots = []
    if load_settings().get("models_dir"):
        roots.append(Path(load_settings()["models_dir"]))
    if sys.platform == "win32":
        for letter in "CDEFG":
            roots.append(Path(f"{letter}:/GrepLens/Models"))
    else:
        roots.append(Path.home() / "GrepLens" / "models")
    return roots


def auto_register() -> dict:
    """Use finished model files already on the computer for any job that has no model yet (e.g. after skipping setup)."""
    current = installed()
    missing = [r for r in ("fast", "review", "embed") if not current.get(r)]
    if not missing:
        return current
    files = [c for c in detect_existing() if c["source"] == "Models folder"]
    for model in manifest():  # manifest order puts the recommended model for each job first
        if model["role"] not in missing:
            continue
        hit = next((c for c in files if c["model_id"] == model["id"] and c["size_gb"] >= 0.3), None)
        if hit:
            current = use_model(model["id"], hit["path"], "found")
            missing.remove(model["role"])
            _log(f"auto: using {hit['path']} for {model['role']}")
    return current


def unfinished() -> list[dict]:
    """Downloads that stopped part-way (for example before a reinstall) and can continue."""
    out, seen = [], set()
    for root in model_folders():
        if not root.is_dir():
            continue
        for part in root.glob("*.gguf.part"):
            name = part.name[:-5].lower().replace("_", "-")
            for model in manifest():
                if all(re.search(rf"(?<![0-9a-z]){re.escape(k)}", name) for k in model["match"]) and model["id"] not in seen \
                        and not installed().get(model["role"], {}).get("id") == model["id"]:
                    seen.add(model["id"])
                    out.append({"model_id": model["id"], "name": model["name"], "folder": str(root),
                                "done_gb": round(part.stat().st_size / 1024 ** 3, 2)})
    return out


def detect_existing() -> list[dict]:
    """Models already on the device that match the manifest and can be reused instead of downloaded."""
    candidates = _ollama_models() + _gguf_files()
    matches = []
    for model in manifest():
        for cand in candidates:
            label = cand["label"].lower().replace("_", "-")
            hit = cand.get("ollama_name") in model.get("ollama", []) or all(
                re.search(rf"(?<![0-9a-z]){re.escape(k)}", label) for k in model["match"])
            if hit and "mmproj" not in label:
                matches.append({**cand, "model_id": model["id"], "role": model["role"]})
    return matches


def scan_folder(folder: str) -> list[dict]:
    """Remember a folder holding model files and return the models found there and elsewhere."""
    if not Path(folder).is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    save_settings({"models_dir": folder})
    return detect_existing()


def installed() -> dict:
    return load_settings().get("models", {})


def use_model(model_id: str, path: str, source: str, ollama_name: str | None = None) -> dict:
    model = by_id(model_id)
    models = installed()
    models[model["role"]] = {"id": model_id, "name": model["name"], "path": path, "source": source,
                             **({"ollama_name": ollama_name} if ollama_name else {})}
    save_settings({"models": models})
    return models


# ------------------------------------------------------------------ downloads
HEADERS = {"User-Agent": "GrepLens (+https://github.com/swaidi/greplens)"}


def _log(message: str) -> None:
    """Download diagnostics: logs/downloads.log in the data folder."""
    from .config import data_dir
    with open(data_dir() / "logs" / "downloads.log", "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")


def resolve_file(model: dict) -> dict:
    """Find the file on Hugging Face with its exact size and SHA-256."""
    tree = httpx.get(f"{HF}/api/models/{model['repo']}/tree/main", timeout=20, follow_redirects=True,
                     headers=HEADERS).json()
    for item in tree:
        if item.get("type") == "file" and fnmatch.fnmatch(item["path"], model["file"]):
            lfs = item.get("lfs") or {}
            return {"path": item["path"], "size": lfs.get("size") or item.get("size"), "sha256": lfs.get("oid")}
    raise FileNotFoundError(f"{model['file']} not found in {model['repo']}")


def progress() -> dict:
    return {"paused": _pause.is_set(), "items": _state}


def start_downloads(model_ids: list[str], folder: str) -> dict:
    global _thread
    Path(folder).mkdir(parents=True, exist_ok=True)
    save_settings({"models_dir": folder})
    for mid in model_ids:
        if _state.get(mid, {}).get("status") not in ("downloading", "done"):
            _state[mid] = {"status": "queued", "done_bytes": 0, "total": 0, "speed": 0, "message": ""}
    _pause.clear()
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_download_all, args=(model_ids, Path(folder)), daemon=True)
        _thread.start()
    return progress()


def pause(flag: bool) -> dict:
    global _thread
    if flag:
        _pause.set()
        for st in _state.values():  # shown at once, even while a connection is still waiting
            if st["status"] in ("queued", "downloading"):
                st.update(status="paused", speed=0, message="Paused")
    else:
        _pause.clear()
        pending = [m for m, s in _state.items() if s["status"] in ("paused", "queued", "error")]
        for m in pending:
            _state[m].update(status="queued", message="")
        if pending and (_thread is None or not _thread.is_alive()):
            _thread = threading.Thread(target=_download_all, args=(pending, Path(load_settings()["models_dir"])),
                                       daemon=True)
            _thread.start()
    return progress()


def _download_all(model_ids: list[str], folder: Path) -> None:
    for mid in model_ids:
        if _pause.is_set():
            return
        if _state[mid]["status"] == "done":
            continue
        try:
            _download(by_id(mid), folder, _state[mid])
        except Exception as exc:
            _log(f"{mid}: failed: {exc!r}")
            _state[mid].update(status="error", speed=0, message=str(exc))


def _download(model: dict, folder: Path, st: dict) -> None:
    st.update(status="downloading", message="Contacting Hugging Face")
    info = resolve_file(model)
    target = folder / Path(info["path"]).name
    part = target.with_suffix(target.suffix + ".part")
    st["total"] = info["size"] or 0
    _log(f"{model['id']}: {info['path']} size={info['size']} sha256={info['sha256']}")
    if not target.exists():
        url = f"{HF}/{model['repo']}/resolve/main/{info['path']}?download=true"
        attempt = 0
        while True:
            have = part.stat().st_size if part.exists() else 0
            st["done_bytes"] = have
            headers = {**HEADERS, **({"Range": f"bytes={have}-"} if have else {})}
            try:
                st["message"] = "Connecting"
                with httpx.stream("GET", url, headers=headers, follow_redirects=True,
                                  timeout=httpx.Timeout(30, connect=20)) as resp:
                    _log(f"{model['id']}: HTTP {resp.status_code} from {resp.url.host} (resume at {have})")
                    if resp.status_code == 416:
                        break
                    resp.raise_for_status()
                    mode = "ab" if have and resp.status_code == 206 else "wb"
                    done = have if mode == "ab" else 0
                    st["message"] = "Downloading"
                    window_start, window_bytes = time.time(), done
                    with open(part, mode) as fh:
                        for chunk in resp.iter_bytes(256 * 1024):
                            fh.write(chunk)
                            done += len(chunk)
                            st["done_bytes"] = done
                            now = time.time()
                            if now - window_start >= 1:
                                st["speed"] = (done - window_bytes) / (now - window_start)
                                window_start, window_bytes = now, done
                            if _pause.is_set():
                                st.update(status="paused", speed=0, message="Paused")
                                return
                break
            except httpx.HTTPError as exc:  # includes a read timeout: no data for 30 seconds
                if _pause.is_set():
                    st.update(status="paused", speed=0, message="Paused")
                    return
                progressed = (part.stat().st_size if part.exists() else 0) > have
                attempt = 1 if progressed else attempt + 1
                _log(f"{model['id']}: attempt {attempt} interrupted: {exc!r}")
                if attempt >= 8:
                    raise RuntimeError("No data received from Hugging Face after several retries. "
                                       "You can download the file in your browser and use Reuse.") from exc
                st.update(speed=0, message=f"No data received, retrying ({attempt}/8)")
                time.sleep(min(30, 2 ** attempt))
        st.update(status="verifying", speed=0, message="Checking file integrity")
        if info["sha256"] and _sha256(part) != info["sha256"]:
            part.unlink(missing_ok=True)
            raise RuntimeError("Checksum mismatch: the file was corrupted and has been removed. Please retry.")
        os.replace(part, target)
    st.update(status="done", done_bytes=st["total"], speed=0, message="Verified")
    _log(f"{model['id']}: done, verified")
    use_model(model["id"], str(target), "download")
    if model["role"] == "embed":  # documents waiting for the search model can be indexed now
        from . import db, ingest
        for doc in db.query("SELECT id FROM documents WHERE status = 'needs_model'"):
            ingest.enqueue(doc["id"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            digest.update(block)
    return digest.hexdigest()


def updates_available() -> list[dict]:
    """Roles whose recommended model in the (remote) manifest differs from the installed one."""
    current = installed()
    out = []
    for role, info in current.items():
        recommended = next((m for m in manifest() if m["role"] == role and m.get("recommended", True)), None)
        if recommended and recommended["id"] != info.get("id") and info.get("id") != "qwen3-4b":
            out.append({"role": role, "installed": info.get("name"), "new": recommended["name"],
                        "size_gb": recommended["size_gb"], "id": recommended["id"]})
    return out
