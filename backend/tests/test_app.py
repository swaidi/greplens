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


def test_ink_note_and_word_export(client, pdf_doc):
    ink = [{"color": "#D92D20", "width": 2.5, "points": [[0.2, 0.2], [0.3, 0.25], [0.4, 0.22]]}]
    note = client.post("/api/notes", json={"document_id": pdf_doc["id"], "mark": "ink", "page": 1, "ink": ink,
                                           "body": "Circle this", "location": "Page 1"}).json()
    assert note["ink"][0]["points"][1] == [0.3, 0.25]
    resp = client.get(f"/api/documents/{pdf_doc['id']}/export.docx")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert any(n.startswith("word/media/") for n in zf.namelist())
        assert "Circle this" in zf.read("word/comments.xml").decode()


def test_scan_folder(client, tmp_path):
    (tmp_path / "Qwen_Qwen3-14B-Q4_K_M.gguf").write_bytes(b"GGUF")
    (tmp_path / "bge-m3-Q8_0.gguf").write_bytes(b"GGUF")
    found = client.post("/api/models/scan", json={"folder": str(tmp_path)}).json()["existing"]
    ids = {f["model_id"] for f in found if f["path"].startswith(str(tmp_path))}
    assert ids == {"qwen3-14b", "bge-m3"}
    assert client.get("/api/settings").json()["models_dir"] == str(tmp_path)
    assert client.post("/api/models/scan", json={"folder": str(tmp_path / "missing")}).status_code == 400


def test_handwritten_page_note_exports(client, pdf_doc):
    hw = [{"color": "#1D4F91", "width": 2.5, "points": [[0.1, 0.05], [0.3, 0.12], [0.5, 0.06]]}]
    note = client.post("/api/notes", json={"document_id": pdf_doc["id"], "page": 1, "location": "Page 1",
                                           "body": "", "category": "info", "handwriting": hw}).json()
    assert note["handwriting"][0]["points"][2] == [0.5, 0.06]
    with zipfile.ZipFile(io.BytesIO(client.get(f"/api/documents/{pdf_doc['id']}/export.docx").content)) as zf:
        rels = zf.read("word/_rels/comments.xml.rels").decode()
    assert "image" in rels


def test_question_saved_while_answering(client, pdf_doc, monkeypatch):
    from greplens import qa
    seen = {}

    def slow_answer(*args):
        seen["rows"] = client.get(f"/api/documents/{pdf_doc['id']}/chat").json()
        raise RuntimeError("engine stopped")
    monkeypatch.setattr(qa, "_answer", slow_answer)
    with pytest.raises(RuntimeError):
        qa.ask(pdf_doc["id"], "Pending question?")
    assert seen["rows"][-1]["content"] == "Pending question?" and seen["rows"][-1]["meta"]["pending"]
    rows = client.get(f"/api/documents/{pdf_doc['id']}/chat").json()
    assert all(r["content"] != "Pending question?" for r in rows)   # removed again after a failure


def wait_job(client, job_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error", "cancelled"):
            return j
        time.sleep(0.3)
    raise AssertionError("job did not finish")


def run_job(client, doc_id, kind, params=None):
    j = client.post(f"/api/documents/{doc_id}/jobs", json={"kind": kind, "params": params or {}}).json()
    j = wait_job(client, j["id"])
    assert j["status"] == "done", j["message"]
    return j


def test_summary_scopes(client, pdf_doc):
    whole = run_job(client, pdf_doc["id"], "summary", {"scope": "all", "length": "brief"})
    assert whole["result"]["text"] and whole["result"]["pages"].startswith("Page")
    one = run_job(client, pdf_doc["id"], "summary", {"scope": "page", "start": 1})
    assert one["result"]["pages"] == "Page 1"


def test_outline_and_section_summary(client, pdf_doc):
    items = client.get(f"/api/documents/{pdf_doc['id']}/outline").json()
    labels = [i["label"] for i in items]
    assert "Article 5" in labels and "Clause 5.1" in labels
    art5 = next(i for i in items if i["label"] == "Article 5")
    assert len(art5["segment_ids"]) >= 2
    sec = run_job(client, pdf_doc["id"], "summary", {"segment_ids": art5["segment_ids"], "section_title": "Article 5"})
    assert sec["result"]["section"] == "Article 5"


def test_translate_document_to_word(client, pdf_doc):
    j = run_job(client, pdf_doc["id"], "translate", {"scope": "all"})
    assert j["result"]["target"] == "ar" and j["result"]["count"] > 3
    resp = client.get(f"/api/jobs/{j['id']}/file")
    assert resp.status_code == 200 and resp.content[:2] == b"PK"


def test_mindmap_and_quiz(client, pdf_doc):
    mm = run_job(client, pdf_doc["id"], "mindmap", {})
    assert mm["result"]["children"] and "title" in mm["result"]["children"][0]
    qz = run_job(client, pdf_doc["id"], "quiz", {"count": 3})
    q = qz["result"]["questions"][0]
    assert len(q["options"]) == 4 and 0 <= q["answer"] < 4 and q["location"]


def test_contract_details_and_new_draft(client, pdf_doc):
    j = run_job(client, pdf_doc["id"], "contract_details", {})
    items = j["result"]["items"]
    values = {i["value"] for i in items}
    assert "SAR 1,250,000" in values and "10%" in values and "[VALUE]" in values
    amount = next(i for i in items if i["value"] == "SAR 1,250,000")
    assert amount["count"] == 2 and amount["locations"]
    resp = client.post(f"/api/documents/{pdf_doc['id']}/contract-draft",
                       json={"replacements": [{"old": "SAR 1,250,000", "new": "SAR 2,000,000"}, {"old": "[VALUE]", "new": ""}]})
    d = docx.Document(io.BytesIO(resp.content))
    text = "\n".join(p.text for p in d.paragraphs)
    assert "SAR 2,000,000" in text and "SAR 1,250,000" not in text and "[VALUE]" in text
    highlighted = [r.text for p in d.paragraphs for r in p.runs if r.font.highlight_color]
    assert "SAR 2,000,000" in highlighted and "[VALUE]" in highlighted


def test_tables_to_excel(client, workspace, tmp_path):
    import openpyxl
    pdf = pymupdf.open()
    page = pdf.new_page()
    rows = [["Item", "Description", "Qty", "Rate"], ["1", "Chiller", "2", "1,250,000"], ["2", "Pump", "4", "35,500"]]
    x0, y0, cw, rh = 72, 100, 110, 24
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            page.draw_rect(pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh), color=(0, 0, 0), width=0.8)
            page.insert_text((x0 + c * cw + 4, y0 + r * rh + 16), val, fontsize=10)
    path = tmp_path / "boq.pdf"
    pdf.save(path)
    with open(path, "rb") as fh:
        doc = client.post(f"/api/workspaces/{workspace['id']}/documents", files={"file": ("boq.pdf", fh)}).json()
    wait_ready(client, doc["id"])
    j = run_job(client, doc["id"], "tables", {"scope": "all"})
    assert j["result"]["tables"] == 1
    book = openpyxl.load_workbook(io.BytesIO(client.get(f"/api/jobs/{j['id']}/file").content))
    sheet = book["Table 1"]
    assert sheet["B2"].value == "Chiller" and sheet["D2"].value == 1250000


def test_redaction_removes_text(client, pdf_doc):
    added = client.post(f"/api/documents/{pdf_doc['id']}/redact/search", json={"term": "1,250,000"}).json()["added"]
    assert added == 2
    data = client.get(f"/api/documents/{pdf_doc['id']}/redacted.pdf").content
    with pymupdf.open("pdf", data) as red:
        text = "".join(p.get_text() for p in red)
        assert "1,250,000" not in text and "Contract Value" in text
        assert not red.metadata.get("producer")
    client.delete(f"/api/documents/{pdf_doc['id']}/redact")


def test_notes_saved_into_pdf(client, pdf_doc):
    data = client.get(f"/api/documents/{pdf_doc['id']}/annotated.pdf").content
    with pymupdf.open("pdf", data) as out:
        kinds = {a.type[1] for p in out for a in p.annots()}
    assert {"Highlight", "Underline", "Ink", "Text"} <= kinds


def test_handwriting_read_and_note_search(client, workspace, pdf_doc):
    assert client.get("/api/handwriting/status").json()["available"] is True
    hw = [{"color": "#17202B", "width": 2.5, "points": [[0.1, 0.05], [0.3, 0.08], [0.5, 0.06]]}]
    note = client.post("/api/notes", json={"document_id": pdf_doc["id"], "page": 1, "body": "", "handwriting": hw}).json()
    assert note["handwriting_text"] == "handwritten note" and note["handwriting_lang"] == "en"
    # read again in Arabic, then correct the text by hand
    note = client.post(f"/api/notes/{note['id']}/read-handwriting", json={"lang": "ar"}).json()
    assert note["handwriting_text"] == "ملاحظة بخط اليد" and note["handwriting_lang"] == "ar"
    note = client.patch(f"/api/notes/{note['id']}", json={"handwriting_text": "check guarantee expiry"}).json()
    assert note["handwriting_text"] == "check guarantee expiry"
    # changed handwriting is read again, in the note's language
    note = client.patch(f"/api/notes/{note['id']}", json={"handwriting": hw + hw}).json()
    assert note["handwriting_text"] == "ملاحظة بخط اليد"
    client.post("/api/notes", json={"document_id": pdf_doc["id"], "page": 1, "body": "Confirm the bank guarantee",
                                    "tags": ["finance"]})
    url = f"/api/workspaces/{workspace['id']}/notes/search"
    assert any(n["id"] == note["id"] for n in client.get(url, params={"q": "ملاحظه"}).json())  # Arabic letter forms ignored
    found = client.get(url, params={"q": "bank guarantee", "document_id": pdf_doc["id"]}).json()
    assert found and found[0]["document_name"] == "contract.pdf"
    assert client.get(url, params={"q": "finance"}).json()
    assert client.get(url, params={"q": "nothing like this"}).json() == []
    # Word export carries the text the handwriting reads as
    import io
    import zipfile
    data = client.get(f"/api/documents/{pdf_doc['id']}/export.docx").content
    assert "Handwriting reads: ملاحظة بخط اليد" in zipfile.ZipFile(io.BytesIO(data)).read("word/comments.xml").decode()


def test_undo_restore_note_and_view_setting(client, pdf_doc):
    note = client.post("/api/notes", json={"document_id": pdf_doc["id"], "page": 1, "body": "to undo", "tags": ["x"],
                                           "mark": "highlight", "rects": [[0.1, 0.1, 0.2, 0.02]]}).json()
    client.delete(f"/api/notes/{note['id']}")
    back = client.post("/api/notes/restore", json=note | {"document_name": "ignored"}).json()
    assert back["id"] == note["id"] and back["body"] == "to undo" and back["tags"] == ["x"] and back["rects"] == note["rects"]
    assert client.patch("/api/settings", json={"view": "margin"}).json()["view"] == "margin"


def test_sticky_note_and_pen_setting(client, pdf_doc):
    note = client.post("/api/notes", json={"document_id": pdf_doc["id"], "page": 1, "body": "Sticky here",
                                           "sticky": {"x": 0.4, "y": 0.3, "open": True}}).json()
    assert note["sticky"] == {"x": 0.4, "y": 0.3, "open": True}
    moved = client.patch(f"/api/notes/{note['id']}", json={"sticky": {"x": 0.5, "y": 0.6, "open": False}}).json()
    assert moved["sticky"]["open"] is False and moved["sticky"]["x"] == 0.5
    plain = client.post("/api/notes", json={"document_id": pdf_doc["id"], "page": 1, "body": "no sticky"}).json()
    assert client.get(f"/api/documents/{pdf_doc['id']}/notes").json()[-1]["sticky"] is None or plain["sticky"] is None
    import pymupdf
    data = client.get(f"/api/documents/{pdf_doc['id']}/annotated.pdf").content
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        texts = [a.info.get("content", "") for a in pdf[0].annots()]
    assert any("Sticky here" in t for t in texts)
    assert client.patch("/api/settings", json={"pen_mode": "highlight"}).json()["pen_mode"] == "highlight"


def test_online_ai_connect_route_and_keep_local(client, workspace, pdf_doc, monkeypatch):
    from greplens import online
    store = {}
    monkeypatch.setattr("greplens.keystore.get", lambda name: store.get(name))
    monkeypatch.setattr("greplens.keystore.put", lambda name, value: store.__setitem__(name, value))
    monkeypatch.setattr("greplens.keystore.delete", lambda name: store.pop(name, None))
    assert online.detect("sk-ant-api03-x") == "anthropic" and online.detect("AIzaXYZ") == "gemini"
    assert online.detect("sk-or-v1-x") == "openrouter" and online.detect("sk-proj-x") == "openai"
    bad = client.post("/api/ai/online/connect", json={"key": "hello"})
    assert bad.status_code == 422 and "doesn't look like a key" in bad.json()["detail"]
    wrong = client.post("/api/ai/online/connect", json={"key": "sk-ant-api03-abc", "provider": "gemini"})
    assert wrong.status_code == 422 and "This key is for Claude" in wrong.json()["detail"]
    st = client.post("/api/ai/online/connect", json={"key": " sk-ant-api03-abcdefghijk "}).json()
    assert st["provider"] == "anthropic" and st["source"] == "online" and st["key_hint"].startswith("sk-ant-")
    assert "abcdefghijk" not in str(client.get("/api/settings").json())  # the key is never in the settings file
    # questions go online, and usage is counted
    client.post(f"/api/documents/{pdf_doc['id']}/ask", json={"question": "What is the contract value?"})
    assert client.get("/api/ai/online").json()["usage"]["requests"] >= 1
    # a workspace kept on this computer never goes online
    before = client.get("/api/ai/online").json()["usage"]["requests"]
    client.patch(f"/api/workspaces/{workspace['id']}", json={"local_only": 1})
    client.post(f"/api/documents/{pdf_doc['id']}/ask", json={"question": "What is the advance payment?"})
    assert client.get("/api/ai/online").json()["usage"]["requests"] == before
    assert client.post("/api/ai/source", json={"source": "local"}).json()["source"] == "local"
    assert client.post("/api/open-url", json={"url": "https://example.com"}).status_code == 400
    assert client.post("/api/ai/online/disconnect").json()["provider"] is None
    assert client.post("/api/ai/source", json={"source": "online"}).status_code == 409


def test_online_model_choice():
    from greplens.online import _choose
    ant = _choose("anthropic", ["claude-haiku-4-5", "claude-sonnet-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"])
    assert ant == {"fast": "claude-haiku-5-5", "review": "claude-sonnet-5-5"}
    oai = _choose("openai", ["gpt-4o", "gpt-5", "gpt-5-mini", "gpt-5.5", "gpt-5.5-mini", "gpt-5.5-nano", "gpt-5.5-pro",
                             "gpt-realtime", "gpt-5-codex", "gpt-image-1"])
    assert oai == {"fast": "gpt-5.5-mini", "review": "gpt-5.5"}
    gem = _choose("gemini", ["gemini-2.5-flash", "gemini-3-flash-preview", "gemini-3-flash", "gemini-3-pro-preview",
                             "gemini-3-flash-lite", "gemini-embedding-001", "gemini-2.5-pro"])
    assert gem == {"fast": "gemini-3-flash", "review": "gemini-3-pro-preview"}
    orr = _choose("openrouter", ["anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5", "google/gemini-3-flash"])
    assert orr == {"fast": "anthropic/claude-haiku-5.5", "review": "anthropic/claude-sonnet-5.5"}


def test_online_requests_adapt_and_explain(monkeypatch):
    import httpx
    from greplens import online
    sent = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append((url, dict(json)))
        if "anthropic" in url:
            assert headers["x-api-key"] == "k" and json["system"].startswith("Be brief")
            return httpx.Response(200, json={"content": [{"type": "text", "text": "Hello"}], "usage": {"input_tokens": 5, "output_tokens": 2}})
        if "temperature" in json and "gpt-9" in json["model"]:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'temperature' is not supported with this model."}})
        if "credit" in json["model"]:
            return httpx.Response(429, json={"error": {"message": "You exceeded your current quota, please check your plan and billing details."}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hi"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}})

    monkeypatch.setattr(httpx, "post", fake_post)
    msgs = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Hi"}]
    assert online._send("anthropic", "k", "claude-haiku-5-5", msgs, max_tokens=50, temperature=0.2)[0] == "Hello"
    assert online._send("openai", "k", "gpt-9-mini", msgs, max_tokens=50, temperature=0.2)[0] == "Hi"
    assert "temperature" not in sent[-1][1] and len(sent) == 3  # retried once without the setting
    try:
        online._send("openrouter", "k", "credit-test", msgs, max_tokens=50, temperature=0.2)
        raise AssertionError("expected an error")
    except online.OnlineError as exc:
        assert "no credit" in str(exc)


def test_models_found_after_skipping_setup(client, tmp_path):
    from greplens import models
    from greplens.config import save_settings
    folder = tmp_path / "Models"
    folder.mkdir()
    (folder / "Qwen_Qwen3-8B-Q4_K_M.gguf").write_bytes(b"\0" * (400 * 1024 * 1024 // 1024))  # small stand-in
    (folder / "bge-m3-Q8_0.gguf.part").write_bytes(b"\0" * 1024)
    save_settings({"models_dir": str(folder), "models": {}})
    import greplens.models as m
    orig = m.detect_existing
    m.detect_existing = lambda: [dict(c, size_gb=4.7) for c in orig()]
    try:
        info = client.get("/api/models").json()
    finally:
        m.detect_existing = orig
    assert info["installed"]["fast"]["id"] == "qwen3-8b" and info["installed"]["fast"]["source"] == "found"
    assert [u["model_id"] for u in info["unfinished"]] == ["bge-m3"]
    assert info["default_folder"] == str(folder)
    save_settings({"models_dir": "", "models": {}})
