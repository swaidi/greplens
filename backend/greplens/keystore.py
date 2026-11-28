"""Keep API keys out of plain files: Windows Credential Manager, or a private file elsewhere."""
from __future__ import annotations

import json
import os
import sys

from .config import data_dir

TARGET = "GrepLens/{name}"


def _file():
    return data_dir() / "secrets.json"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    _adv = ctypes.WinDLL("advapi32", use_last_error=True)
    _adv.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    _adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    _adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _adv.CredFree.argtypes = [ctypes.c_void_p]
    _GENERIC, _LOCAL_MACHINE = 1, 2

    def get(name: str) -> str | None:
        ptr = ctypes.POINTER(_CREDENTIAL)()
        if not _adv.CredReadW(TARGET.format(name=name), _GENERIC, 0, ctypes.byref(ptr)):
            return None
        try:
            cred = ptr.contents
            return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize).decode("utf-16-le")
        finally:
            _adv.CredFree(ptr)

    def put(name: str, value: str) -> None:
        blob = value.encode("utf-16-le")
        buf = (ctypes.c_byte * len(blob)).from_buffer_copy(blob)
        cred = _CREDENTIAL(Type=_GENERIC, TargetName=TARGET.format(name=name), CredentialBlobSize=len(blob),
                           CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)), Persist=_LOCAL_MACHINE,
                           UserName="GrepLens")
        if not _adv.CredWriteW(ctypes.byref(cred), 0):
            raise OSError(ctypes.get_last_error(), "Windows could not save the key.")

    def delete(name: str) -> None:
        _adv.CredDeleteW(TARGET.format(name=name), _GENERIC, 0)

else:  # Linux and others: a file only this user can read
    def _load() -> dict:
        try:
            return json.loads(_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(values: dict) -> None:
        path = _file()
        path.write_text(json.dumps(values), encoding="utf-8")
        os.chmod(path, 0o600)

    def get(name: str) -> str | None:
        return _load().get(name)

    def put(name: str, value: str) -> None:
        values = _load()
        values[name] = value
        _save(values)

    def delete(name: str) -> None:
        values = _load()
        values.pop(name, None)
        _save(values)
