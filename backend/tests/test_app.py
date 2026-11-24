"""End-to-end API tests with the deterministic stand-in AI (GREPLENS_FAKE_AI=1)."""
import io
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path

os.environ["GREPLENS_FAKE_AI"] = "1"
os.environ["GREPLENS_DATA"] = tempfile.mkdtemp(prefix="greplens-test-")

import docx  # noqa: E402
import pymupdf  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from greplens import main, ocr  # noqa: E402

CONTRACT = [
    "Article 4 - Contract Value",
    "4.1 The Contract Value for the Works is SAR [VALUE] (in words: [VALUE IN WORDS]), inclusive of VAT.",
    "4.2 The Contract Value is a lump sum and shall not be adjusted except through approved Variation Orders.",
    "Article 5 - Advance Payment",
    "5.1 The Employer shall pay an advance payment of 10% of the Contract Value against an unconditional "
    "bank guarantee of equal value. The guarantee amount is SAR 1,250,000.",
    "5.2 The advance payment is recovered from each interim payment as stated in Clause 9.4.",
    "Article 6 - Time for Completion",
    "6.1 The Contractor shall complete the Works within [12] months from the Commencement Date.",
    "6.2 Delay damages apply at 0.1% per day, up to a maximum of 10% of the Contract Value of SAR 1,250,000.",
]


def make_pdf(path: Path, scanned_page: bool = True) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in CONTRACT:
        page.insert_textbox(pymupdf.Rect(72, y, 520, y + 60), line, fontsize=11)
        y += 64
    if scanned_page:  # an image-only page, like a scan, to exercise the GrepAll OCR path
        src = pymupdf.open()
        p = src.new_page()
        p.insert_text((72, 100), "Article 7 - Insurance. The Contractor shall insure the Works.", fontsize=16)
        pix = p.get_pixmap(dpi=200)
        scan = doc.new_page()
        scan.insert_image(scan.rect, pixmap=pix)
    doc.save(path)


def make_docx(path: Path) -> None:
    d = docx.Document()
    for line in CONTRACT:
        d.add_paragraph(line)
    d.save(path)


def wait_ready(client, doc_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        doc = client.get(f"/api/documents/{doc_id}").json()
        if doc["status"] in ("ready", "error"):
            return doc
        time.sleep(0.3)
    raise AssertionError("document not ready")


def wait_review(client, review_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/api/reviews/{review_id}").json()
        if r["status"] in ("done", "error", "paused"):
            return r
        time.sleep(0.3)
    raise AssertionError("review did not finish")


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="module")
def workspace(client):
    return client.post("/api/workspaces", json={"name": "Sample MEP Contract", "categories": ["legal", "engineering"]}).json()


@pytest.fixture(scope="module")
def pdf_doc(client, workspace, tmp_path_factory):
    path = tmp_path_factory.mktemp("docs") / "contract.pdf"
    make_pdf(path)
    with open(path, "rb") as fh:
        doc = client.post(f"/api/workspaces/{workspace['id']}/documents", files={"file": ("contract.pdf", fh)}).json()
    return wait_ready(client, doc["id"])


def test_pdf_ingest_locations_and_ocr(client, pdf_doc):
    assert pdf_doc["status"] == "ready", pdf_doc["message"]
    segs = client.get(f"/api/documents/{pdf_doc['id']}/segments").json()
    s51 = next(s for s in segs if s["text"].startswith("5.1"))
    assert s51["location"] == "Article 5, Clause 5.1, Paragraph 1, Page 1"
    if ocr.available():
        assert pdf_doc["ocr_pages"] == 1
        assert any("Insurance" in s["text"] and s["page"] == 2 for s in segs)


def test_docx_ingest(client, workspace, tmp_path):
    path = tmp_path / "contract.docx"
    make_docx(path)
    with open(path, "rb") as fh:
        doc = client.post(f"/api/workspaces/{workspace['id']}/documents", files={"file": ("contract.docx", fh)}).json()
    doc = wait_ready(client, doc["id"])
    assert doc["status"] == "ready"
    segs = client.get(f"/api/documents/{doc['id']}/segments").json()
    assert any(s["location"].startswith("Article 6, Clause 6.2") and "≈ Page" in s["location"] for s in segs)
    data = client.get(f"/api/documents/{doc['id']}/export.docx")
    assert data.status_code == 200 and data.content[:2] == b"PK"


def test_ask_modes(client, pdf_doc):
    a = client.post(f"/api/documents/{pdf_doc['id']}/ask",
                    json={"question": "What is the cap on delay damages?", "mode": "category",
                          "categories": ["legal", "engineering"]}).json()
    assert a["found"] and a["citations"] and a["citations"][0]["location"]
    assert a["general_knowledge"]
    b = client.post(f"/api/documents/{pdf_doc['id']}/ask",
                    json={"question": "And the advance payment?", "mode": "doc"}).json()
    assert b["general_knowledge"] == ""
    assert len(client.get(f"/api/documents/{pdf_doc['id']}/chat").json()) == 4


def test_notes_crud(client, pdf_doc):
    segs = client.get(f"/api/documents/{pdf_doc['id']}/segments").json()
    s51 = next(s for s in segs if s["text"].startswith("5.1"))
    note = client.post("/api/notes", json={"document_id": pdf_doc["id"], "mark": "highlight", "color": "yellow",
                                           "page": 1, "rects": [[0.1, 0.2, 0.3, 0.02]], "segment_id": s51["id"],
                                           "quote": "unconditional bank guarantee", "body": "Check validity",
                                           "category": "risk", "tags": ["guarantee"]}).json()
    assert note["location"].startswith("Article 5, Clause 5.1")
    upd = client.patch(f"/api/notes/{note['id']}", json={"status": "resolved"}).json()
    assert upd["status"] == "resolved" and upd["tags"] == ["guarantee"]
    client.post("/api/notes", json={"document_id": pdf_doc["id"], "mark": "underline", "color": "orange", "page": 1,
                                    "quote": "up to a maximum of 10%", "body": "Sole remedy?", "category": "query"})
    assert len(client.get(f"/api/documents/{pdf_doc['id']}/notes").json()) == 2


def test_review_verifies_and_cross_checks(client, pdf_doc):
    r = client.post(f"/api/documents/{pdf_doc['id']}/reviews",
                    json={"categories": ["legal", "engineering"], "mode": "open"}).json()
    r = wait_review(client, r["id"])
    assert r["status"] == "done", r.get("message")
    titles = [f["title"] for f in r["findings"]]
    assert "Invented" not in titles                     # finding with a fake quote was rejected
    assert "Sample finding" in titles                   # verified finding kept, with its location
    assert any(f["title"] == "Value or text left blank" for f in r["findings"])
    assert any("9.4" in f["title"] for f in r["findings"])  # missing cross-reference
    assert any(f["title"] == "Same amount stated more than once" for f in r["findings"])
    assert all(f["location"] for f in r["findings"])


def test_checklist_and_instruction_modes(client, pdf_doc):
    for body in ({"mode": "checklist", "checklist": ["Values stated once"]},
                 {"mode": "instruction", "instruction": "Check all values match Article 4."}):
        r = wait_review(client, client.post(f"/api/documents/{pdf_doc['id']}/reviews", json=body).json()["id"])
        assert r["status"] == "done"


def test_translate_and_glossary(client):
    client.post("/api/glossary", json={"en": "Variation Order", "ar": "أمر تغيير"})
    hit = client.post("/api/translate", json={"text": "Variation Orders"}).json()
    assert hit["glossary"] and hit["translation"] == "أمر تغيير"
    ar = client.post("/api/translate", json={"text": "فترة ضمان العيوب"}).json()
    assert ar["source"] == "ar" and ar["target"] == "en"


def test_word_export_pdf_source(client, pdf_doc):
    resp = client.get(f"/api/documents/{pdf_doc['id']}/export.docx")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        comments = zf.read("word/comments.xml").decode()
    assert "Check validity" in comments and "Recommendation" in comments


def test_package_roundtrip(client, workspace, pdf_doc):
    data = client.get(f"/api/workspaces/{workspace['id']}/package").content
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("manifest.json"))
    assert manifest["format"] == "greplens-package" and manifest["notes"]
    again = client.post("/api/packages/import", files={"file": ("ws.greplens", data)}).json()
    assert again["notes"] == 0 and again["documents"] == 0   # merge adds nothing twice


def test_revision_carries_notes(client, workspace, pdf_doc, tmp_path):
    path = tmp_path / "contract_v2.pdf"
    make_pdf(path, scanned_page=False)
    with open(path, "rb") as fh:
        doc = client.post(f"/api/workspaces/{workspace['id']}/documents", files={"file": ("contract_v2.pdf", fh)},
                          data={"revision_of": pdf_doc["id"]}).json()
    wait_ready(client, doc["id"])
    notes = client.get(f"/api/documents/{doc['id']}/notes").json()
    assert len(notes) == 2 and notes[0]["rects"]


def test_hardware_and_models(client, monkeypatch, tmp_path):
    hw = client.get("/api/hardware").json()
    assert hw["ram_gb"] > 0 and hw["recommendation"]["models"]
    root = tmp_path / "ollama"
    (root / "manifests/registry.ollama.ai/library/qwen3").mkdir(parents=True)
    (root / "blobs").mkdir()
    (root / "blobs/sha256-abc").write_bytes(b"GGUF")
    (root / "manifests/registry.ollama.ai/library/qwen3/8b").write_text(json.dumps(
        {"layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:abc"}]}))
    monkeypatch.setenv("OLLAMA_MODELS", str(root))
    from greplens import models
    found = models.detect_existing()
    assert any(f["model_id"] == "qwen3-8b" and f["source"] == "Ollama" for f in found)
