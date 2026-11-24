"""Background jobs for longer AI tasks (summaries, translation, mind maps, quizzes, contract details, tables).

Each job runs in its own thread, reports progress, can be cancelled, and keeps its result (and an
output file when there is one) so it is still there after switching documents or restarting.
"""
from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Callable

from . import db
from .ingest import doc_folder

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, document_id TEXT, kind TEXT, params TEXT, status TEXT, progress REAL DEFAULT 0,
  message TEXT DEFAULT '', result TEXT, file TEXT, created REAL, updated REAL);
CREATE INDEX IF NOT EXISTS job_doc ON jobs(document_id, kind);
"""
db.JSON_FIELDS.update({"params", "result"})
HANDLERS: dict[str, Callable] = {}


class Cancelled(Exception):
    pass


class Job:
    """Handle passed to a task: report progress, check for cancellation, name output files."""

    def __init__(self, row: dict):
        self.id, self.document_id, self.params = row["id"], row["document_id"], row["params"] or {}

    def progress(self, fraction: float, message: str = "") -> None:
        current = db.get("jobs", self.id)
        if current and current["status"] == "cancelling":
            raise Cancelled()
        db.update("jobs", self.id, {"progress": round(max(0.0, min(1.0, fraction)), 3), "message": message,
                                    "updated": db.now()})

    def output(self, name: str) -> Path:
        folder = doc_folder(self.document_id) / "outputs"
        folder.mkdir(exist_ok=True)
        return folder / f"{self.id[:8]}-{name}"


def init() -> None:
    with db.connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("UPDATE jobs SET status = 'error', message = 'Interrupted when GrepLens closed. Run it again.' "
                     "WHERE status IN ('queued', 'running', 'cancelling')")


def task(kind: str):
    def register(fn):
        HANDLERS[kind] = fn
        return fn
    return register


def start(document_id: str, kind: str, params: dict) -> dict:
    if kind not in HANDLERS:
        raise KeyError(f"Unknown task: {kind}")
    row = db.insert("jobs", {"document_id": document_id, "kind": kind, "params": params, "status": "queued",
                             "progress": 0, "created": db.now(), "updated": db.now()})
    threading.Thread(target=_run, args=(row,), daemon=True, name=f"job-{kind}").start()
    return get(row["id"])


def _run(row: dict) -> None:
    job = Job(row)
    db.update("jobs", job.id, {"status": "running", "updated": db.now()})
    try:
        result = HANDLERS[row["kind"]](job) or {}
        file = result.pop("_file", None)
        db.update("jobs", job.id, {"status": "done", "progress": 1, "message": "", "result": result,
                                   "file": str(file) if file else None, "updated": db.now()})
    except Cancelled:
        db.update("jobs", job.id, {"status": "cancelled", "message": "Stopped", "updated": db.now()})
    except Exception as exc:
        traceback.print_exc()
        db.update("jobs", job.id, {"status": "error", "message": str(exc), "updated": db.now()})


def get(job_id: str) -> dict | None:
    return db.get("jobs", job_id)


def cancel(job_id: str) -> None:
    db.execute("UPDATE jobs SET status = 'cancelling' WHERE id = ? AND status IN ('queued', 'running')", (job_id,))


def latest(document_id: str) -> dict[str, dict]:
    rows = db.query("SELECT * FROM jobs WHERE document_id = ? ORDER BY created", (document_id,))
    return {r["kind"]: r for r in rows}
