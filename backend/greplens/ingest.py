"""Import pipeline: save → OCR scanned pages (GrepAll) → extract with locations → index.

Runs one document at a time on a background worker so the interface stays responsive.
"""
from __future__ import annotations

import hashlib
import queue
import shutil
import threading
import traceback
from pathlib import Path

import pymupdf

from . import db, extract, index, ocr
from .config import data_dir

_jobs: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None


def doc_folder(document_id: str) -> Path:
    folder = data_dir() / "documents" / document_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def original_path(doc: dict) -> Path:
    return doc_folder(doc["id"]) / f"original{Path(doc['filename']).suffix.lower()}"


def view_path(doc: dict) -> Path:
    """The file shown in the viewer: the OCR copy when scanned pages were processed."""
    ocr_copy = doc_folder(doc["id"]) / "ocr.pdf"
    return ocr_copy if ocr_copy.exists() else original_path(doc)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def add_document(workspace_id: str, filename: str, source: Path, revision_of: str | None = None,
                 document_id: str | None = None) -> dict:
    kind = extract.kind_of(filename)
    if not kind:
        raise ValueError(f"Unsupported file type: {Path(filename).suffix}")
    doc = {"id": document_id or db.new_id(), "workspace_id": workspace_id, "filename": filename, "kind": kind,
           "status": "queued", "progress": 0, "created": db.now(), "revision_of": revision_of}
    target = original_path(doc)
    shutil.copyfile(source, target)
    doc["sha256"] = sha256_of(target)
    db.insert("documents", doc)
    enqueue(doc["id"])
    return doc


def enqueue(document_id: str) -> None:
    global _worker
    _jobs.put(document_id)
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_run, daemon=True, name="ingest")
        _worker.start()


def _set(doc_id: str, status: str, progress: float, message: str = "", **extra) -> None:
    db.update("documents", doc_id, {"status": status, "progress": round(progress, 3), "message": message, **extra})


def _run() -> None:
    while True:
        try:
            doc_id = _jobs.get(timeout=5)
        except queue.Empty:
            return
        try:
            process(doc_id)
        except Exception as exc:  # keep the worker alive; report the error on the document
            traceback.print_exc()
            _set(doc_id, "error", 0, str(exc))


def process(doc_id: str) -> None:
    doc = db.get("documents", doc_id)
    if not doc:
        return
    src = original_path(doc)
    ocr_pages = 0
    if doc["kind"] == "pdf":
        with pymupdf.open(src) as pdf:
            scanned = sum(1 for page in pdf if ocr.needs_ocr(page))
        if scanned and ocr.available():
            _set(doc_id, "ocr", 0.05, f"Reading {scanned} scanned pages")
            ocr_pages = ocr.ocr_pdf(src, doc_folder(doc_id) / "ocr.pdf",
                                    lambda n, t: _set(doc_id, "ocr", 0.05 + 0.45 * n / t, f"OCR page {n} of {t}"))
        elif scanned:
            _set(doc_id, "extract", 0.1, f"{scanned} scanned pages were skipped: OCR language data not found")
    _set(doc_id, "extract", 0.5, "Reading text")
    segments, pages = extract.extract(view_path(doc) if doc["kind"] == "pdf" else src, doc["kind"])
    db.execute("DELETE FROM segments WHERE document_id = ?", (doc_id,))
    rows = [{"id": db.new_id(), "document_id": doc_id, "seq": i, "page": s.page, "approx_page": int(s.approx_page),
             "article": s.article, "clause": s.clause, "paragraph": s.paragraph, "location": s.location,
             "text": s.text, "bbox": s.bbox} for i, s in enumerate(segments)]
    db.insert_many("segments", rows)
    from .config import FAKE_AI
    from . import models
    if not FAKE_AI and not models.auto_register().get("embed"):
        # no search model yet: say so plainly and wait; indexing starts by itself once it is downloaded
        _set(doc_id, "needs_model", 0.55, "The document search model (BGE-M3) is needed to index this document.",
             pages=pages, ocr_pages=ocr_pages)
        return
    _set(doc_id, "index", 0.55, "Indexing", pages=pages, ocr_pages=ocr_pages)
    index.delete_document(doc["workspace_id"], doc_id)
    index.index_document(doc["workspace_id"], doc_id, rows,
                         lambda n, t: _set(doc_id, "index", 0.55 + 0.45 * n / t, f"Indexing {n} of {t}"))
    if doc.get("revision_of"):
        carry_over_notes(doc["revision_of"], doc_id)
    _set(doc_id, "ready", 1.0, "")


def carry_over_notes(old_id: str, new_id: str) -> int:
    """Copy notes from a previous version to its revision, re-anchoring each one on its quoted text."""
    from .review import locate

    new_doc = db.get("documents", new_id)
    segments = segments_of(new_id)
    moved = 0
    pdf = pymupdf.open(view_path(new_doc)) if new_doc["kind"] == "pdf" else None
    try:
        for note in db.query("SELECT * FROM notes WHERE document_id = ?", (old_id,)):
            seg = locate(note["quote"] or "", segments) if note["quote"] else None
            if note["quote"] and not seg:
                continue  # the text no longer exists in the revision
            rects, page = [], seg["page"] if seg else note["page"]
            if pdf and seg and seg["page"]:
                pg = pdf[seg["page"] - 1]
                w, h = pg.rect.width, pg.rect.height
                rects = [[r.x0 / w, r.y0 / h, (r.x1 - r.x0) / w, (r.y1 - r.y0) / h]
                         for r in pg.search_for(note["quote"][:200])]
            db.insert("notes", {**note, "id": db.new_id(), "document_id": new_id, "page": page,
                                "segment_id": seg["id"] if seg else None, "rects": rects or note["rects"],
                                "location": seg["location"] if seg else note["location"], "created": db.now()})
            moved += 1
    finally:
        if pdf:
            pdf.close()
    return moved


def segments_of(document_id: str) -> list[dict]:
    return db.query("SELECT * FROM segments WHERE document_id = ? ORDER BY seq", (document_id,))
