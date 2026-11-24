"""Questions and answers about a document, with citations.

Modes:
  category - answer from the document, then add separately labelled general knowledge from the
             selected categories (never mixed with document facts)
  doc      - Doc Only: answer strictly from the document, or say it is not found
"""
from __future__ import annotations

import json

from . import db, index, llm, profiles

SYSTEM = (
    "You are GrepLens, a careful document assistant. You receive numbered SOURCES taken from one document. "
    "Reply in the same language as the QUESTION. Return only JSON with these keys:\n"
    '  "found": true or false - whether the SOURCES answer the question\n'
    '  "from_document": the answer using only the SOURCES (empty when not found)\n'
    '  "citations": list of the source numbers you used\n'
    '  "general_knowledge": {gk_rule}\n'
    "Never invent facts about the document. Quote figures exactly as written in the SOURCES."
)
GK_CATEGORY = ("short extra context from the viewpoint of {personas}; general knowledge only, "
               "clearly separate from the document; empty if nothing useful")
GK_DOC_ONLY = 'always ""'


def ask(document_id: str, question: str, mode: str = "category", categories: list[str] | None = None) -> dict:
    doc = db.get("documents", document_id)
    if not doc:
        raise KeyError("document not found")
    history = db.query("SELECT role, content, meta FROM chats WHERE document_id = ? ORDER BY created DESC LIMIT 6",
                       (document_id,))[::-1]
    asked = db.insert("chats", {"document_id": document_id, "role": "user", "content": question, "meta": {"pending": True},
                                "created": db.now()})
    try:
        return _answer(doc, document_id, question, mode, categories, history, asked)
    except Exception:
        db.execute("DELETE FROM chats WHERE id = ?", (asked["id"],))
        raise


def _answer(doc, document_id, question, mode, categories, history, asked) -> dict:
    last_question = next((h["content"] for h in reversed(history) if h["role"] == "user"), "")
    search = question if len(question) > 40 or not last_question else f"{last_question}\n{question}"
    sources = index.retrieve(doc["workspace_id"], document_id, search, k=6)

    chosen = profiles.get_many(categories or [])
    personas = " and ".join(p["persona"] for p in chosen)
    gk_rule = GK_DOC_ONLY if mode == "doc" else GK_CATEGORY.format(personas=personas)
    numbered = "\n\n".join(f"[{i}] ({s['location']})\n{s['text']}" for i, s in enumerate(sources, 1))
    messages = [{"role": "system", "content": SYSTEM.format(gk_rule=gk_rule)}]
    for turn in history:
        content = turn["content"]
        if turn["role"] == "assistant":
            meta = turn["meta"] or {}
            content = meta.get("from_document") or ("Not found in the document." if not meta.get("found") else content)
        messages.append({"role": turn["role"], "content": content})
    messages.append({"role": "user", "content": f"SOURCES:\n{numbered or '(none)'}\n\nQUESTION: {question}"})

    def fake() -> str:
        if not sources:
            return json.dumps({"found": False, "from_document": "", "citations": [], "general_knowledge": ""})
        return json.dumps({"found": True, "from_document": sources[0]["text"][:300], "citations": [1],
                           "general_knowledge": "" if mode == "doc" else f"Context for {personas}."})

    raw = llm.chat("fast", messages, json_mode=True, max_tokens=900, fake=fake)
    data = llm.parse_json(raw, {"found": bool(raw.strip()), "from_document": raw, "citations": [], "general_knowledge": ""})
    found = bool(data.get("found")) and bool(str(data.get("from_document", "")).strip())
    cited = []
    for n in data.get("citations") or []:
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(sources) and n not in cited:
            cited.append(n)
    if found and not cited and sources:
        cited = [1]
    answer = {
        "found": found,
        "from_document": str(data.get("from_document", "")).strip() if found else "",
        "general_knowledge": "" if mode == "doc" else str(data.get("general_knowledge", "")).strip(),
        "citations": [{"n": n, "location": sources[n - 1]["location"], "page": sources[n - 1]["page"],
                       "segment_id": sources[n - 1]["segment_ids"][0]} for n in cited],
        "mode": mode, "categories": [p["name"] for p in chosen], "searched_pages": doc.get("pages", 0),
    }
    db.update("chats", asked["id"], {"meta": {}})
    reply = db.insert("chats", {"document_id": document_id, "role": "assistant",
                                "content": answer["from_document"] or "Not found in the document.",
                                "meta": answer, "created": db.now()})
    answer["id"] = reply["id"]
    return answer


def history(document_id: str) -> list[dict]:
    """Conversation so far; a question still being answered has meta.pending = true."""
    return db.query("SELECT * FROM chats WHERE document_id = ? ORDER BY created", (document_id,))


def clear(document_id: str) -> None:
    db.execute("DELETE FROM chats WHERE document_id = ?", (document_id,))
