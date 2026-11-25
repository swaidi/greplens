"""Freeze the Python back end into one executable for the Tauri shell (a "sidecar").

Run from the backend folder after building the interface (frontend: npm run build):
    python build_backend.py
Output: ../src-tauri/binaries/greplens-backend-<target-triple>[.exe]
"""
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "src-tauri" / "binaries"


def target_triple() -> str:
    try:
        return subprocess.check_output(["rustc", "--print", "host-tuple"], text=True).strip()
    except Exception:
        machine = platform.machine().lower()
        arch = "x86_64" if machine in ("amd64", "x86_64") else "aarch64"
        return {"win32": f"{arch}-pc-windows-msvc", "darwin": f"{arch}-apple-darwin"}.get(sys.platform, f"{arch}-unknown-linux-gnu")


def main() -> None:
    if not (HERE / "greplens" / "static" / "index.html").exists():
        sys.exit("Build the interface first: cd ../frontend && npm ci && npm run build")
    sep = ";" if sys.platform == "win32" else ":"
    import docx
    # python-docx opens templates via docx/parts/../templates; the folder must exist in the bundle
    docx_parts = Path(docx.__file__).parent / "parts" / "__init__.py"
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--name", "greplens-backend",
        "--add-data", f"greplens/static{sep}greplens/static",
        "--add-data", f"greplens/profiles/builtin.json{sep}greplens/profiles",
        "--add-data", f"greplens/profiles/checklists.json{sep}greplens/profiles",
        "--collect-all", "chromadb", "--collect-all", "llama_index.core", "--collect-all", "llama_index.vector_stores.chroma",
        "--collect-all", "langgraph", "--collect-submodules", "uvicorn", "--collect-data", "docx", "--add-data", f"{docx_parts}{sep}docx/parts", "--collect-data", "pptx",
        "--copy-metadata", "chromadb", "--copy-metadata", "llama-index-core", "--copy-metadata", "opentelemetry-sdk",
        "--hidden-import", "tiktoken_ext.openai_public", "--hidden-import", "tiktoken_ext", "--collect-data", "tiktoken",
        "--hidden-import", "pymupdf", "--hidden-import", "openpyxl",
        # optional cloud AI providers: LiteLLM and the secure key store (Windows Credential Manager)
        "--collect-all", "litellm", "--copy-metadata", "litellm", "--copy-metadata", "keyring",
        "--collect-submodules", "keyring.backends",
        "--exclude-module", "torch", "--exclude-module", "tensorflow", "--exclude-module", "matplotlib",
    ]
    if sys.platform == "win32":
        args.append("--noconsole")
    args.append("run_backend.py")
    subprocess.check_call(args, cwd=HERE)
    OUT.mkdir(parents=True, exist_ok=True)
    exe = ".exe" if sys.platform == "win32" else ""
    target = OUT / f"greplens-backend-{target_triple()}{exe}"
    shutil.copy2(HERE / "dist" / f"greplens-backend{exe}", target)
    print(f"Sidecar ready: {target}")


if __name__ == "__main__":
    main()
