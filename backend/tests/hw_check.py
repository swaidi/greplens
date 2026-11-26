"""Windows build check: can the handwriting recognizer be reached, from source and from the frozen back end?"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from greplens import handwriting  # noqa: E402

print("source status:", json.dumps(handwriting.status(), ensure_ascii=False))
# a short scribble shaped like the letters "lo"
strokes = [{"points": [[0.10, 0.02], [0.10, 0.10], [0.10, 0.18]]},
           {"points": [[0.20, 0.12], [0.17, 0.15], [0.20, 0.18], [0.23, 0.15], [0.20, 0.12]]}]
try:
    print("source read:", repr(handwriting.recognize(strokes, "en")))
except Exception as exc:  # noqa: BLE001
    print("source read failed:", repr(exc))

exe = next((HERE.parent / "src-tauri" / "binaries").glob("greplens-backend-*.exe"))
env = {**os.environ, "GREPLENS_DATA": tempfile.mkdtemp(), "GREPLENS_PORT": "8799"}
proc = subprocess.Popen([str(exe)], env=env)
try:
    for _ in range(120):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8799/api/handwriting/status", timeout=2) as r:
                print("frozen status:", r.read().decode())
                break
        except OSError:
            time.sleep(1)
    else:
        print("frozen back end did not start")
finally:
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
