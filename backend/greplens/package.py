"""Workspace packages (.greplens): share a workspace's documents, notes and findings with colleagues.

Import merges into an existing copy of the same workspace: new notes and documents are added, nothing
already present is overwritten, and every note keeps its author.
"""
from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path

from . import __version__, db, ingest

FORMAT = "greplens-package"


def export(workspace_id: str) -> tuple[bytes, str]:
    ws = db.get("workspaces", workspace_id)
    docs = db.query("SELECT * FROM documents WHERE workspace_id = ?", (workspace_id,))
    notes = db.query("SELECT * FROM notes WHERE workspace_id = ?", (workspace_id,))
    reviews, findings = [], []
    for d in docs:
        reviews += db.query("SELECT * FROM reviews WHERE document_id = ?", (d["id"],))
        findings += db.query("SELECT * FROM findings WHERE document_id = ?", (d["id"],))
    manifest = {"format": FORMAT, "version": 1, "app": __version__, "workspace": ws,
                "documents": [{k: d[k] for k in ("id", "filename", "kind", "sha256", "revision_of", "created")} for d in docs],
                "notes": notes, "reviews": reviews, "findings": findings,
                "instructions": db.query("SELECT * FROM instructions WHERE workspace_id = ?", (workspace_id,))}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        for d in docs:
            zf.write(ingest.original_path(d), f"documents/{d['id']}/{ingest.original_path(d).name}")
    return buf.getvalue(), f"{ws['name']}.greplens"


def import_package(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        if manifest.get("format") != FORMAT:
            raise ValueError("This file is not a GrepLens workspace package.")
        ws = manifest["workspace"]
        added = {"documents": 0, "notes": 0, "findings": 0}
        if not db.get("workspaces", ws["id"]):
            db.insert("workspaces", {**ws, "provider": "local", "updated": db.now()})  # each user picks their own AI
        doc_map: dict[str, str] = {}
        for d in manifest["documents"]:
            existing = db.query("SELECT id FROM documents WHERE workspace_id = ? AND sha256 = ?", (ws["id"], d["sha256"]))
            if existing:
                doc_map[d["id"]] = existing[0]["id"]
                continue
            member = next(n for n in zf.namelist() if n.startswith(f"documents/{d['id']}/"))
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / Path(member).name
                src.write_bytes(zf.read(member))
                new = ingest.add_document(ws["id"], d["filename"], src, d.get("revision_of"),
                                          document_id=None if db.get("documents", d["id"]) else d["id"])
            doc_map[d["id"]] = new["id"]
            added["documents"] += 1
        for n in manifest["notes"]:
            if not db.get("notes", n["id"]):
                db.insert("notes", {**n, "document_id": doc_map.get(n["document_id"], n["document_id"])})
                added["notes"] += 1
        for r in manifest.get("reviews", []):
            if not db.get("reviews", r["id"]):
                db.insert("reviews", {**r, "document_id": doc_map.get(r["document_id"], r["document_id"]),
                                      "status": "done" if r["status"] == "done" else "paused"})
        for f in manifest.get("findings", []):
            if not db.get("findings", f["id"]):
                db.insert("findings", {**f, "document_id": doc_map.get(f["document_id"], f["document_id"])})
                added["findings"] += 1
        for ins in manifest.get("instructions", []):
            if not db.get("instructions", ins["id"]):
                db.insert("instructions", {**ins, "workspace_id": ws["id"]})
    return {"workspace_id": ws["id"], "name": ws["name"], **added}
