"""GrepLens local API (FastAPI). Serves the interface and listens on 127.0.0.1 only."""
from __future__ import annotations

import mimetypes
import os
import shutil
import tempfile
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, db, engine, jobs, tools, export_docx, handwriting, hardware, ingest, models, package, profiles, qa, review, translate, updates
from .config import API_PORT, load_settings, save_settings

@asynccontextmanager
async def lifespan(_app):
    db.init()
    jobs.init()
    for doc in db.query("SELECT id FROM documents WHERE status NOT IN ('ready', 'error')"):
        ingest.enqueue(doc["id"])  # finish imports interrupted by closing the app
    review.resume_interrupted()
    yield
    engine.stop()


mimetypes.add_type("text/javascript", ".mjs")  # pdf.js worker must be served as JavaScript

app = FastAPI(title="GrepLens", version=__version__, lifespan=lifespan)


@app.exception_handler(engine.EngineError)
def _engine_error(_req, exc):
    return JSONResponse({"detail": str(exc), "code": "engine"}, status_code=503)


def _must(table: str, row_id: str) -> dict:
    row = db.get(table, row_id)
    if not row:
        raise HTTPException(404, f"{table[:-1]} not found")
    return row


def _download(data: bytes, filename: str, media: str) -> Response:
    return Response(data, media_type=media,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


# ------------------------------------------------------------------ app, setup, models
@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/api/settings")
def get_settings():
    return load_settings()


@app.patch("/api/settings")
def patch_settings(body: dict):
    allowed = {"language", "engine", "threads", "update_check", "author", "setup_complete", "models_dir", "theme"}
    return save_settings({k: v for k, v in body.items() if k in allowed})


@app.get("/api/hardware")
def get_hardware():
    info = hardware.summary()
    return {**info, "recommendation": hardware.recommend(info["ram_gb"]),
            "measured": load_settings().get("speeds", {})}


@app.get("/api/models")
def get_models():
    return {"manifest": models.manifest(), "installed": models.installed(), "existing": models.detect_existing(),
            "engine": engine.status(), "updates": models.updates_available()}


class DownloadBody(BaseModel):
    ids: list[str]
    folder: str


@app.post("/api/models/download")
def download_models(body: DownloadBody):
    return models.start_downloads(body.ids, body.folder)


@app.get("/api/models/progress")
def model_progress():
    return models.progress()


@app.post("/api/models/pause")
def pause_models(body: dict):
    return models.pause(bool(body.get("paused", True)))


class ScanBody(BaseModel):
    folder: str


@app.post("/api/models/scan")
def scan_models(body: ScanBody):
    try:
        return {"existing": models.scan_folder(body.folder.strip().strip('"')), "folder": body.folder}
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc


class ReuseBody(BaseModel):
    model_id: str
    path: str
    source: str = "existing"
    ollama_name: str | None = None


@app.post("/api/models/reuse")
def reuse_model(body: ReuseBody):
    if not os.path.isfile(body.path):
        raise HTTPException(400, "File not found")
    return models.use_model(body.model_id, body.path, body.source, body.ollama_name)


@app.get("/api/updates")
def check_updates(force: bool = False):
    return updates.check(force)


@app.post("/api/updates/later")
def update_later():
    updates.remind_later()
    return {"ok": True}


@app.post("/api/updates/install")
def update_install(body: dict):
    if not body.get("asset"):
        raise HTTPException(400, "This release has no installer for this system.")
    return updates.start_install(body["asset"])


@app.get("/api/updates/progress")
def update_progress():
    return updates.progress()


# ------------------------------------------------------------------ workspaces and documents
class WorkspaceBody(BaseModel):
    name: str
    categories: list[str] = []


@app.get("/api/workspaces")
def list_workspaces():
    rows = db.query("SELECT * FROM workspaces ORDER BY updated DESC")
    for ws in rows:
        ws["documents"] = db.query("SELECT COUNT(*) AS n FROM documents WHERE workspace_id = ?", (ws["id"],))[0]["n"]
        ws["notes"] = db.query("SELECT COUNT(*) AS n FROM notes WHERE workspace_id = ?", (ws["id"],))[0]["n"]
        ws["findings"] = db.query("SELECT COUNT(*) AS n FROM findings f JOIN documents d ON d.id = f.document_id "
                                  "WHERE d.workspace_id = ?", (ws["id"],))[0]["n"]
        ws["authors"] = db.query("SELECT COUNT(DISTINCT author) AS n FROM notes WHERE workspace_id = ?", (ws["id"],))[0]["n"]
    return rows


@app.post("/api/workspaces")
def create_workspace(body: WorkspaceBody):
    return db.insert("workspaces", {"name": body.name.strip() or "Untitled", "categories": body.categories[:2],
                                    "created": db.now(), "updated": db.now()})


@app.patch("/api/workspaces/{ws_id}")
def update_workspace(ws_id: str, body: dict):
    _must("workspaces", ws_id)
    db.update("workspaces", ws_id, {k: v for k, v in body.items() if k in ("name", "categories")} | {"updated": db.now()})
    return db.get("workspaces", ws_id)


@app.delete("/api/workspaces/{ws_id}")
def delete_workspace(ws_id: str):
    for doc in db.query("SELECT id FROM documents WHERE workspace_id = ?", (ws_id,)):
        delete_document(doc["id"])
    db.execute("DELETE FROM notes WHERE workspace_id = ?", (ws_id,))
    db.execute("DELETE FROM workspaces WHERE id = ?", (ws_id,))
    return {"ok": True}


@app.get("/api/workspaces/{ws_id}")
def get_workspace(ws_id: str):
    ws = _must("workspaces", ws_id)
    ws["documents"] = db.query("SELECT * FROM documents WHERE workspace_id = ? ORDER BY created", (ws_id,))
    db.update("workspaces", ws_id, {"updated": db.now()})
    return ws


@app.post("/api/workspaces/{ws_id}/documents")
async def upload(ws_id: str, file: UploadFile = File(...), revision_of: str | None = Form(None)):
    _must("workspaces", ws_id)
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
    try:
        return ingest.add_document(ws_id, file.filename, Path(tmp.name), revision_of or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        os.unlink(tmp.name)


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: str):
    return _must("documents", doc_id)


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str):
    doc = _must("documents", doc_id)
    from . import index
    index.delete_document(doc["workspace_id"], doc_id)
    for table in ("segments", "notes", "findings", "reviews", "chats"):
        db.execute(f"DELETE FROM {table} WHERE document_id = ?", (doc_id,))
    db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    shutil.rmtree(ingest.doc_folder(doc_id), ignore_errors=True)
    return {"ok": True}


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: str):
    doc = _must("documents", doc_id)
    return FileResponse(ingest.view_path(doc), filename=doc["filename"])


@app.get("/api/documents/{doc_id}/segments")
def document_segments(doc_id: str):
    return ingest.segments_of(doc_id)


# ------------------------------------------------------------------ notes
class NoteBody(BaseModel):
    document_id: str
    mark: str | None = None          # highlight | underline | None (plain note)
    color: str | None = None
    page: int | None = None
    rects: list = []
    segment_id: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    quote: str = ""
    body: str = ""
    category: str | None = None      # risk | query | clarification | ...
    status: str = "open"
    tags: list[str] = []
    ai_answer: bool = False
    location: str | None = None
    handwriting: list = []           # handwritten note: strokes on the note pad, x 0..1, y 0..pad height/width
    handwriting_text: str = ""       # the handwriting read as text by Windows (searchable, editable)
    handwriting_lang: str = ""       # en | ar: the language the handwriting is read in
    ink: list = []                   # freehand strokes: [{"color", "width", "points": [[x, y], ...]}] (page-relative 0..1)


@app.get("/api/documents/{doc_id}/notes")
def list_notes(doc_id: str):
    return db.query("SELECT * FROM notes WHERE document_id = ? ORDER BY page, created", (doc_id,))


@app.post("/api/notes")
def create_note(body: NoteBody):
    doc = _must("documents", body.document_id)
    values = body.model_dump()
    if not values.get("location") and values.get("segment_id"):
        seg = db.get("segments", values["segment_id"])
        values["location"] = seg["location"] if seg else ""
    values.update(workspace_id=doc["workspace_id"], author=load_settings().get("author", "You"),
                  ai_answer=int(body.ai_answer), created=db.now(), updated=db.now())
    error = ""
    if values.get("handwriting") and not values.get("handwriting_text"):
        values["handwriting_lang"] = values.get("handwriting_lang") or _hw_lang()
        values["handwriting_text"], error = _read_handwriting(values["handwriting"], values["handwriting_lang"])
    return db.insert("notes", values) | ({"handwriting_error": error} if error else {})


def _hw_lang() -> str:
    return "ar" if load_settings().get("language") == "ar" else "en"


def _read_handwriting(strokes: list, lang: str) -> tuple[str, str]:
    """Read handwriting as text; a failure never blocks saving the note."""
    try:
        return handwriting.recognize(strokes, lang), ""
    except Exception as exc:  # noqa: BLE001
        return "", str(exc) or exc.__class__.__name__


@app.patch("/api/notes/{note_id}")
def update_note(note_id: str, body: dict):
    note = _must("notes", note_id)
    allowed = {"body", "category", "status", "tags", "color", "mark", "ink", "handwriting", "handwriting_text", "handwriting_lang"}
    values = {k: v for k, v in body.items() if k in allowed}
    error = ""
    if "handwriting" in values and "handwriting_text" not in values:  # new or changed handwriting: read it again
        lang = values.get("handwriting_lang") or note.get("handwriting_lang") or _hw_lang()
        values["handwriting_lang"] = lang
        values["handwriting_text"], error = _read_handwriting(values["handwriting"], lang)
    db.update("notes", note_id, values | {"updated": db.now()})
    return db.get("notes", note_id) | ({"handwriting_error": error} if error else {})


@app.post("/api/notes/{note_id}/read-handwriting")
def read_handwriting(note_id: str, body: dict):
    """Read a note's handwriting again, for example in the other language."""
    note = _must("notes", note_id)
    lang = str(body.get("lang") or note.get("handwriting_lang") or _hw_lang())
    text, error = _read_handwriting(note.get("handwriting") or [], lang)
    if error:
        raise HTTPException(422, error)
    db.update("notes", note_id, {"handwriting_text": text, "handwriting_lang": lang, "updated": db.now()})
    return db.get("notes", note_id)


@app.get("/api/handwriting/status")
def handwriting_status():
    return handwriting.status()


@app.get("/api/workspaces/{ws_id}/notes/search")
def search_notes(ws_id: str, q: str, document_id: str | None = None):
    """Find notes by their text, quote, tags, location or handwriting read as text (Arabic letter forms ignored)."""
    words = review.normalize(q).split()
    if not words:
        return []
    sql = ("SELECT n.*, d.filename AS document_name FROM notes n JOIN documents d ON d.id = n.document_id "
           "WHERE n.workspace_id = ? AND (n.mark IS NULL OR n.mark != 'redact')")
    args: list = [ws_id]
    if document_id:
        sql += " AND n.document_id = ?"
        args.append(document_id)
    found = []
    for n in db.query(sql + " ORDER BY d.created, n.page, n.created", tuple(args)):
        hay = review.normalize(" ".join(str(x or "") for x in (n["body"], n["quote"], n["handwriting_text"], n["location"],
                                                                 n["category"], " ".join(n["tags"] or []))))
        if all(w in hay for w in words):
            found.append(n)
    return found[:200]


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: str):
    db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return {"ok": True}


# ------------------------------------------------------------------ AI: ask, review, translate
class AskBody(BaseModel):
    question: str
    mode: str = "category"
    categories: list[str] = []


@app.post("/api/documents/{doc_id}/ask")
def ask(doc_id: str, body: AskBody):
    doc = _must("documents", doc_id)
    if doc["status"] != "ready":
        raise HTTPException(409, "The document is still being prepared.")
    return qa.ask(doc_id, body.question, body.mode, body.categories)


@app.get("/api/documents/{doc_id}/chat")
def chat_history(doc_id: str):
    return qa.history(doc_id)


@app.delete("/api/documents/{doc_id}/chat")
def clear_chat(doc_id: str):
    qa.clear(doc_id)
    return {"ok": True}


class ReviewBody(BaseModel):
    categories: list[str] = []
    mode: str = "open"              # open | checklist | instruction
    checklist: list[str] = []
    instruction: str = ""


@app.post("/api/documents/{doc_id}/reviews")
def start_review(doc_id: str, body: ReviewBody):
    doc = _must("documents", doc_id)
    if doc["status"] != "ready":
        raise HTTPException(409, "The document is still being prepared.")
    return review.start(doc_id, body.model_dump())


@app.get("/api/documents/{doc_id}/reviews/latest")
def latest_review(doc_id: str):
    return review.latest(doc_id) or {}


@app.get("/api/reviews/{review_id}")
def get_review(review_id: str):
    return review.get(review_id) or {}


@app.post("/api/reviews/{review_id}/pause")
def pause_review(review_id: str):
    review.pause(review_id)
    return {"ok": True}


@app.post("/api/reviews/{review_id}/resume")
def resume_review(review_id: str):
    review.resume(review_id)
    return {"ok": True}


class TranslateBody(BaseModel):
    text: str
    context: str = ""


@app.post("/api/translate")
def do_translate(body: TranslateBody):
    return translate.translate(body.text[:500], body.context)


@app.get("/api/glossary")
def get_glossary():
    return translate.glossary()


@app.post("/api/glossary")
def add_term(body: dict):
    return db.insert("glossary", {"en": body["en"].strip(), "ar": body["ar"].strip()})


@app.delete("/api/glossary/{term_id}")
def delete_term(term_id: str):
    db.execute("DELETE FROM glossary WHERE id = ?", (term_id,))
    return {"ok": True}


@app.get("/api/profiles")
def get_profiles():
    return profiles.list_profiles()


@app.post("/api/profiles")
def save_profile(body: dict):
    return profiles.save(body)


@app.delete("/api/profiles/{pid}")
def delete_profile(pid: str):
    profiles.delete(pid)
    return {"ok": True}


# ------------------------------------------------------------------ export and sharing
@app.get("/api/documents/{doc_id}/export.docx")
def export_word(doc_id: str):
    _must("documents", doc_id)
    data, name = export_docx.export(doc_id)
    return _download(data, name, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.get("/api/workspaces/{ws_id}/package")
def export_package(ws_id: str):
    _must("workspaces", ws_id)
    data, name = package.export(ws_id)
    return _download(data, name, "application/zip")


@app.post("/api/packages/import")
async def import_package(file: UploadFile = File(...)):
    try:
        return package.import_package(await file.read())
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


# ------------------------------------------------------------------ document tools
class JobBody(BaseModel):
    kind: str          # summary | translate | mindmap | quiz | contract_details | tables
    params: dict = {}


def _ready(doc_id: str) -> dict:
    doc = _must("documents", doc_id)
    if doc["status"] != "ready":
        raise HTTPException(409, "The document is still being prepared.")
    return doc


@app.post("/api/documents/{doc_id}/jobs")
def start_job(doc_id: str, body: JobBody):
    _ready(doc_id)
    try:
        return jobs.start(doc_id, body.kind, body.params)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/documents/{doc_id}/jobs")
def document_jobs(doc_id: str):
    return jobs.latest(doc_id)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return _must("jobs", job_id)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    jobs.cancel(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/file")
def job_file(job_id: str):
    path = tools.output_file(_must("jobs", job_id))
    if not path or not path.exists():
        raise HTTPException(404, "No file for this task.")
    return FileResponse(path, filename=path.name.split("-", 1)[1])


@app.get("/api/documents/{doc_id}/outline")
def document_outline(doc_id: str):
    _ready(doc_id)
    return tools.outline(doc_id)


@app.post("/api/documents/{doc_id}/redact/search")
def redact_search(doc_id: str, body: dict):
    _ready(doc_id)
    return {"added": tools.redact_search(doc_id, str(body.get("term", "")))}


@app.delete("/api/documents/{doc_id}/redact")
def redact_clear(doc_id: str):
    db.execute("DELETE FROM notes WHERE document_id = ? AND mark = 'redact'", (doc_id,))
    return {"ok": True}


@app.get("/api/documents/{doc_id}/redacted.pdf")
def redacted(doc_id: str):
    _ready(doc_id)
    try:
        data, name = tools.redacted_pdf(doc_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _download(data, name, "application/pdf")


@app.get("/api/documents/{doc_id}/annotated.pdf")
def annotated(doc_id: str, findings: bool = True):
    _ready(doc_id)
    try:
        data, name = tools.annotated_pdf(doc_id, findings)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _download(data, name, "application/pdf")


@app.post("/api/documents/{doc_id}/contract-draft")
def contract_draft(doc_id: str, body: dict):
    _ready(doc_id)
    data, name = tools.contract_draft(doc_id, body.get("replacements", []))
    return _download(data, name, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# ------------------------------------------------------------------ interface
STATIC = Path(__file__).resolve().parent / "static"
if STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        file = STATIC / path
        return FileResponse(file if path and file.is_file() else STATIC / "index.html")


def run(open_browser: bool = False) -> None:
    db.init()
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{API_PORT}/")
    uvicorn.run(app, host="127.0.0.1", port=API_PORT, log_level="warning")


if __name__ == "__main__":
    run(open_browser=os.environ.get("GREPLENS_BROWSER") == "1")
