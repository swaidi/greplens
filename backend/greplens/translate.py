"""Arabic ↔ English translation of a selected word or phrase, using the surrounding clause as context."""
from __future__ import annotations

import json
import re

from . import db, llm

AR_RE = re.compile(r"[؀-ۿ]")


def direction(text: str) -> tuple[str, str]:
    letters = re.findall(r"[A-Za-z؀-ۿ]", text)
    arabic = sum(1 for ch in letters if AR_RE.match(ch))
    return ("ar", "en") if letters and arabic / len(letters) > 0.5 else ("en", "ar")


def glossary() -> list[dict]:
    return db.query("SELECT * FROM glossary ORDER BY en COLLATE NOCASE")


def _glossary_hit(text: str, source: str) -> dict | None:
    key = text.strip().lower().rstrip("s") if source == "en" else text.strip()
    for item in glossary():
        if source == "en" and item["en"].strip().lower().rstrip("s") == key:
            return item
        if source == "ar" and item["ar"].strip() == key:
            return item
    return None


def translate(text: str, context: str = "") -> dict:
    source, target = direction(text)
    hit = _glossary_hit(text, source)
    if hit:
        return {"source": source, "target": target, "text": text, "translation": hit[target],
                "glossary": True, "note": ""}
    terms = [g for g in glossary() if (g["en"].lower() in context.lower() or g["ar"] in context)][:20]
    names = {"en": "English", "ar": "Arabic"}
    system = (f"Translate the TEXT from {names[source]} to {names[target]}. Use the CONTEXT only to choose the right "
              "meaning (legal, engineering or academic). Keep numbers and clause references unchanged. "
              'Return only JSON: {"translation": "...", "note": "one short line on meaning, or empty"}')
    if terms:
        system += "\nAlways use these fixed translations: " + "; ".join(f"{t['en']} = {t['ar']}" for t in terms)
    raw = llm.chat("fast", [{"role": "system", "content": system},
                            {"role": "user", "content": f"CONTEXT: {context[:1500]}\n\nTEXT: {text}"}],
                   json_mode=True, max_tokens=300, temperature=0.1,
                   fake=lambda: json.dumps({"translation": f"[{target}] {text}", "note": ""}))
    data = llm.parse_json(raw, {"translation": raw, "note": ""})
    return {"source": source, "target": target, "text": text, "translation": str(data.get("translation", "")).strip(),
            "glossary": False, "note": str(data.get("note", "")).strip()}
