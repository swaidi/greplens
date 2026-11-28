"""Local AI engine: runs the bundled llama.cpp server (llama-server) per model role.

Roles:
  fast   - Q&A, translation (Qwen 8B class)
  review - full document review (Qwen 14B class); falls back to `fast` when not installed
  embed  - BGE-M3 embeddings
With engine = "ollama" in settings, an existing Ollama installation is used instead.
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import threading
import time

import httpx
import psutil

from .config import data_dir, load_settings, resources_dir, save_settings

PORTS = {"fast": 8711, "review": 8712, "embed": 8713}
OLLAMA_URL = "http://127.0.0.1:11434"
_procs: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()
_job = None  # Windows job object: engine processes end automatically when the back end ends


def _tie_to_backend(proc: subprocess.Popen) -> None:
    """On Windows, put the engine in a job object that is closed (and its processes killed) when
    this back end exits for any reason, including End task in Task Manager."""
    global _job
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                        "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        if _job is None:
            job = kernel32.CreateJobObjectW(None, None)
            info = EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
                return
            _job = job
        handle = kernel32.OpenProcess(0x0100 | 0x0001, False, proc.pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if handle:
            kernel32.AssignProcessToJobObject(wintypes.HANDLE(_job), wintypes.HANDLE(handle))
            kernel32.CloseHandle(wintypes.HANDLE(handle))
    except Exception:
        pass  # the engine still stops when the app closes normally


class EngineError(RuntimeError):
    pass


def native_arm64() -> bool:
    """True on Windows on ARM (e.g. Snapdragon), even when GrepLens itself runs as an x64 app under emulation."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        process, native = ctypes.c_ushort(), ctypes.c_ushort()
        k32 = ctypes.windll.kernel32
        if k32.IsWow64Process2(k32.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
            return native.value == 0xAA64  # IMAGE_FILE_MACHINE_ARM64
    except (AttributeError, OSError):
        pass
    return False


def _arm_failed() -> bool:
    return bool(load_settings().get("arm64_engine_failed"))


def server_binary() -> str | None:
    exe = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    folders = ["engine-arm64", "engine"] if native_arm64() and not _arm_failed() else ["engine"]
    bundled = [str(base / f / exe) for f in folders for base in (resources_dir(), resources_dir().parent)]
    for candidate in (os.environ.get("GREPLENS_LLAMA_SERVER", ""), *bundled):
        if candidate and os.path.isfile(candidate):
            return candidate
    return shutil.which("llama-server")


def threads() -> int:
    configured = int(load_settings().get("threads") or 0)
    return configured or max(1, psutil.cpu_count(logical=False) or os.cpu_count() or 4)


def model_for(role: str) -> dict | None:
    models = load_settings().get("models", {})
    if role == "review" and not models.get("review"):
        role = "fast"
    return models.get(role)


def _alive(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def endpoint(role: str) -> tuple[str, str]:
    """Return (OpenAI-compatible base URL, model name) for a role, starting the engine if needed."""
    settings = load_settings()
    model = model_for(role)
    if not model:
        raise EngineError(f"No model is installed for '{role}'. Open Settings > Device & Models.")
    if settings.get("engine") == "ollama" and model.get("ollama_name"):
        return f"{OLLAMA_URL}/v1", model["ollama_name"]
    real_role = "fast" if role == "review" and not settings.get("models", {}).get("review") else role
    port = PORTS[real_role]
    with _lock:
        proc = _procs.get(real_role)
        if proc is None or proc.poll() is not None or not _alive(port):
            _start(real_role, model["path"], port)
    return f"http://127.0.0.1:{port}/v1", os.path.basename(model["path"])


def _start(role: str, model_path: str, port: int) -> None:
    binary = server_binary()
    if not binary:
        raise EngineError("The AI engine (llama-server) was not found in the app folder.")
    if not os.path.isfile(model_path):
        raise EngineError(f"Model file not found: {model_path}")
    args = [binary, "-m", model_path, "--host", "127.0.0.1", "--port", str(port), "-t", str(threads())]
    if role == "embed":
        args += ["--embedding", "--pooling", "cls", "-c", "8192", "-b", "8192", "-ub", "8192"]
    else:
        args += ["-c", "16384"]
    log = open(data_dir() / "logs" / f"engine-{role}.log", "ab")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    _procs[role] = subprocess.Popen(args, stdout=log, stderr=log, creationflags=flags)
    _tie_to_backend(_procs[role])
    deadline = time.time() + 300
    while time.time() < deadline:
        if _procs[role].poll() is not None:
            if "engine-arm64" in binary and not _arm_failed():
                # the native ARM engine could not start here: use the standard engine from now on
                save_settings({"arm64_engine_failed": True})
                return _start(role, model_path, port)
            raise EngineError(f"The AI engine stopped while loading the {role} model. See logs/engine-{role}.log.")
        if _alive(port):
            return
        time.sleep(1)
    raise EngineError("The AI engine took too long to load the model.")


def stop(role: str | None = None) -> None:
    for name in ([role] if role else list(_procs)):
        proc = _procs.pop(name, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()


def status() -> dict:
    return {role: bool(_procs.get(role) and _procs[role].poll() is None) for role in PORTS} | {
        "binary": bool(server_binary()), "native_arm64": bool(server_binary() and "engine-arm64" in server_binary())}


atexit.register(stop)
