"""Device summary for the setup wizard: processor, RAM, GPU, drives, OS, and model recommendations."""
from __future__ import annotations

import platform
import re
import subprocess
import sys

import psutil

GB = 1024 ** 3


def cpu_name() -> str:
    try:
        if sys.platform == "win32":
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        if sys.platform.startswith("linux"):
            for line in open("/proc/cpuinfo", encoding="utf-8"):
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        if sys.platform == "darwin":
            return subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
    except Exception:
        pass
    return platform.processor() or "Unknown processor"


def gpus() -> list[str]:
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(["powershell", "-NoProfile", "-Command",
                                           "(Get-CimInstance Win32_VideoController).Name"], text=True, timeout=10,
                                          creationflags=subprocess.CREATE_NO_WINDOW)
            return [line.strip() for line in out.splitlines() if line.strip()]
        out = subprocess.check_output(["lspci"], text=True, timeout=5)
        return [line.split(":", 2)[-1].strip() for line in out.splitlines() if re.search(r"VGA|3D|Display", line)]
    except Exception:
        return []


def drives() -> list[dict]:
    out, seen = [], set()
    for part in psutil.disk_partitions(all=False):
        if part.mountpoint in seen or "cdrom" in part.opts or part.fstype in ("squashfs", "tmpfs", "overlay"):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        seen.add(part.mountpoint)
        if usage.total < 2 * GB:
            continue
        out.append({"mount": part.mountpoint, "total_gb": round(usage.total / GB, 1), "free_gb": round(usage.free / GB, 1)})
    return out


def summary() -> dict:
    mem = psutil.virtual_memory()
    freq = psutil.cpu_freq()
    gpu_list = gpus()
    dedicated = [g for g in gpu_list if re.search(r"NVIDIA|GeForce|RTX|Radeon RX|Arc A", g, re.I)]
    return {
        "cpu": cpu_name(), "cores": psutil.cpu_count(logical=False) or 0, "threads": psutil.cpu_count() or 0,
        "base_ghz": round((freq.max or freq.current) / 1000, 2) if freq else None,
        "ram_gb": round(mem.total / GB, 1), "ram_available_gb": round(mem.available / GB, 1),
        "gpus": gpu_list, "dedicated_gpu": dedicated[0] if dedicated else None,
        "os": f"{platform.system()} {platform.release()}", "bits": "64-bit" if sys.maxsize > 2 ** 32 else "32-bit",
        "drives": drives(),
    }


def recommend(ram_gb: float) -> dict:
    """Which models suit this RAM, and how each feature is expected to run (estimates until measured)."""
    if ram_gb >= 24:
        models, tier = ["qwen3-8b", "qwen3-14b", "bge-m3"], "high"
    elif ram_gb >= 12:
        models, tier = ["qwen3-8b", "bge-m3"], "mid"
    else:
        models, tier = ["qwen3-4b", "bge-m3"], "low"
    features = {
        "high": [("qa", "rec", "About 6–10 words per second"), ("translate", "rec", "About 1–3 seconds per phrase"),
                 ("review", "slow", "About 3–5 words per second · runs in the background"), ("ocr", "rec", "")],
        "mid": [("qa", "rec", "About 6–10 words per second"), ("translate", "rec", "About 1–3 seconds per phrase"),
                ("review", "slow", "Uses the 8B model · runs in the background"), ("ocr", "rec", "")],
        "low": [("qa", "slow", "About 8–15 words per second, lower quality"), ("translate", "slow", ""),
                ("review", "no", "Not enough memory for reliable reviews"), ("ocr", "rec", "")],
    }[tier]
    return {"tier": tier, "models": models,
            "features": [{"id": f, "status": s, "speed": sp} for f, s, sp in features]}
