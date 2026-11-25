"""Workspace instruction files: rules or reference material the user attaches to a workspace
(for example a contract drafting guide or house style). They are used by Ask, Review and Create.

The text is split into short parts. With the local AI only the parts most relevant to the current task
are added, so the small model keeps room for the document. With a cloud AI the whole file is added
(and, with Claude, cached).
"""
from __future__ import annotations

import io
import re

from . import db, providers

SCHEMA = """
CREATE TABLE IF NOT EXISTS instructions (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, name TEXT NOT NULL, text TEXT NOT NULL,
  enabled INTEGER DEFAULT 1, created REAL);
CREATE INDEX IF NOT EXISTS instr_ws ON instructions(workspace_id);
"""
LOCAL_BUDGET = 3500      # characters of instructions added for the local model
CLOUD_BUDGET = 120_000   # characters for a cloud model (whole files in practice)
PART_CHARS = 900
MAX_FILE_CHARS = 400_000


def init() -> None:
    with db.connect() as conn:
        conn.executescript(SCHEMA)


def read_upload(filename: str, data: bytes) -> str:
    """Plain text from an uploaded instruction file (.txt, .md, .docx or .pdf)."""
    name = filename.lower()
    if name.endswith(".docx"):
        import docx
        document = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs)
    if name.endswith(".pdf"):
        import pymupdf
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            return "\n".join(page.get_text() for page in pdf)
    if name.endswith((".txt", ".md", ".markdown", ".csv")):
        for encoding in ("utf-8-sig", "cp1256", "latin-1"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
    raise ValueError("Use a .txt, .md, .docx or .pdf file for instructions.")


def add(workspace_id: str, name: str, text: str) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("The instruction file is empty.")
    return db.insert("instructions", {"workspace_id": workspace_id, "name": name.strip() or "Instructions",
                                      "text": text[:MAX_FILE_CHARS], "enabled": 1, "created": db.now()})


def list_for(workspace_id: str) -> list[dict]:
    rows = db.query("SELECT * FROM instructions WHERE workspace_id = ? ORDER BY created", (workspace_id,))
    for r in rows:
        r["chars"] = len(r["text"])
    return rows


def _parts(text: str) -> list[str]:
    """Split on headings and blank lines, then pack into parts of about PART_CHARS characters."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n|\n(?=#{1,6} )", text) if b.strip()]
    parts, current, heading = [], "", ""
    for block in blocks:
        if block.startswith("#"):
            heading = block.splitlines()[0].lstrip("# ").strip()
        if current and len(current) + len(block) > PART_CHARS:
            parts.append(current)
            current = f"({heading})\n" if heading and not block.startswith("#") else ""
        current += block + "\n"
    if current.strip():
        parts.append(current)
    return parts


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[\w؀-ۿ]{4,}", text.lower())}


def for_prompt(workspace_id: str | None, task_text: str = "") -> str:
    """Instruction text to add to a prompt, sized for the AI this workspace uses."""
    if not workspace_id:
        return ""
    files = [f for f in list_for(workspace_id) if f["enabled"]]
    if not files:
        return ""
    budget = LOCAL_BUDGET if providers.current() == providers.LOCAL else CLOUD_BUDGET
    total = sum(len(f["text"]) for f in files)
    if total <= budget:
        body = "\n\n".join(f"[{f['name']}]\n{f['text']}" for f in files)
    else:  # pick the parts that share the most words with the task
        wanted = _terms(task_text)
        scored = []
        for f in files:
            for i, part in enumerate(_parts(f["text"])):
                overlap = len(wanted & _terms(part))
                scored.append((overlap, -i, f["name"], part))
        scored.sort(reverse=True)
        chosen, used = [], 0
        for overlap, _i, name, part in scored:
            if used + len(part) > budget:
                continue
            chosen.append(f"[{name}]\n{part.strip()}")
            used += len(part)
        body = "\n\n".join(chosen)
    return ("WORKSPACE INSTRUCTIONS (rules and reference material set by the user for this workspace; "
            "follow them when they apply):\n" + body.strip())


def workspace_of_document(document_id: str) -> str | None:
    doc = db.get("documents", document_id)
    return doc["workspace_id"] if doc else None
