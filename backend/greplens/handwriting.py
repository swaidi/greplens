"""Read handwritten notes as text, offline, with the handwriting recognizer built into Windows.

Strokes come from the note pad: x from 0 to 1 across the pad, y in the same units.
The recognizer is part of Windows (Windows.UI.Input.Inking); it reads English, Arabic and any
other language whose handwriting pack is installed in Windows Settings. Nothing is downloaded.
On other systems, or when no recognizer is installed, recognition is simply not available.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading

log = logging.getLogger("greplens.handwriting")
PAD = 1000.0  # strokes are scaled to a 1000-unit wide pad, close to real ink size in screen units
_lock = threading.Lock()

# words used in the recognizer names Windows reports, in English and in Arabic Windows
_LANG_WORDS = {"en": ("english", "الإنجليزية", "الانجليزية"), "ar": ("arabic", "العربية")}


def _fake() -> bool:
    return os.environ.get("GREPLENS_FAKE_AI") == "1"


def _inking():
    from winrt.windows.ui.input import inking  # type: ignore
    return inking


def status() -> dict:
    """Which handwriting languages Windows can read on this computer."""
    if _fake():
        return {"available": True, "languages": ["en", "ar"], "recognizers": ["Fake recognizer"]}
    if sys.platform != "win32":
        return {"available": False, "languages": [], "recognizers": [], "reason": "Handwriting reading uses Windows."}
    try:
        names = _in_thread(lambda: [r.name for r in _inking().InkRecognizerContainer().get_recognizers()])
    except Exception as exc:  # noqa: BLE001 - recognizer missing or bindings not installed
        log.warning("handwriting recognizer unavailable: %s", exc)
        return {"available": False, "languages": [], "recognizers": [], "reason": str(exc)}
    langs = [k for k, words in _LANG_WORDS.items() if any(w in n.lower() for n in names for w in words)]
    return {"available": bool(names), "languages": langs, "recognizers": names}


def _pick(recognizers, lang: str):
    words = _LANG_WORDS.get(lang, ())
    for r in recognizers:
        if any(w in r.name.lower() for w in words):
            return r
    return None


async def _recognize(strokes: list[dict], lang: str) -> str:
    inking = _inking()
    from winrt.windows.foundation import Point  # type: ignore
    from winrt.windows.foundation.numerics import Matrix3x2  # type: ignore

    builder = inking.InkStrokeBuilder()
    container = inking.InkStrokeContainer()
    identity = Matrix3x2(1, 0, 0, 1, 0, 0)
    for s in strokes:
        pts = [inking.InkPoint(Point(float(x) * PAD, float(y) * PAD), 0.5) for x, y, *_ in s.get("points", [])]
        if len(pts) < 2:
            continue
        container.add_stroke(builder.create_stroke_from_ink_points(pts, identity))
    rc = inking.InkRecognizerContainer()
    chosen = _pick(rc.get_recognizers(), lang)
    if lang and chosen is None:
        raise LookupError(f"Windows has no handwriting recognizer for '{lang}'.")
    if chosen is not None:
        rc.set_default_recognizer(chosen)
    results = await rc.recognize_async(container, inking.InkRecognitionTarget.ALL)
    words = []
    for r in results:
        cands = list(r.get_text_candidates())
        if cands:
            words.append(cands[0])
    return " ".join(words).strip()


def _in_thread(fn, timeout: float = 30):
    """Run a Windows call in a fresh thread with its own COM apartment, one call at a time."""
    out: dict = {}

    def run() -> None:
        try:
            from winrt.runtime import ApartmentType, init_apartment  # type: ignore
            try:
                init_apartment(ApartmentType.MULTI_THREADED)
            except Exception:  # noqa: BLE001 - already initialized
                pass
            out["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            out["error"] = exc

    with _lock:
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout)
    if "error" in out:
        raise out["error"]
    if "value" not in out:
        raise TimeoutError("Windows did not answer in time.")
    return out["value"]


def recognize(strokes: list[dict], lang: str = "en") -> str:
    """Return the text of the handwriting, or raise if Windows cannot read it."""
    if not strokes:
        return ""
    if _fake():
        return "handwritten note" if lang != "ar" else "ملاحظة بخط اليد"
    if sys.platform != "win32":
        raise RuntimeError("Handwriting reading uses Windows.")
    return _in_thread(lambda: asyncio.run(_recognize(strokes, lang)))
