"""Document tools: summaries, outline, whole-document translation, mind map, quiz, contract details and
new drafts, tables to Excel, redaction with sanitizing, and saving notes into the PDF.

Long AI tasks run as background jobs (see jobs.py); quick ones answer directly.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pymupdf

from . import db, llm, translate
from .ingest import segments_of, view_path
from .jobs import Job, task
from .review import build_sections, normalize

AR_RE = re.compile(r"[؀-ۿ]")
GROUP_CHARS = 6000


# ------------------------------------------------------------------ helpers
def pick_segments(document_id: str, params: dict) -> list[dict]:
    """Segments for a scope: whole document, one page, a page range, or given segments (a section)."""
    segs = segments_of(document_id)
    if params.get("segment_ids"):
        wanted = set(params["segment_ids"])
        return [s for s in segs if s["id"] in wanted]
    scope = params.get("scope", "all")
    if scope in ("page", "range"):
        start = int(params.get("start") or 1)
        end = int(params.get("end") or start) if scope == "range" else start
        return [s for s in segs if start <= (s["page"] or 0) <= end]
    return segs


def groups(segments: list[dict], limit: int = GROUP_CHARS) -> list[list[dict]]:
    out, current, size = [], [], 0
    for seg in segments:
        if current and size + len(seg["text"]) > limit:
            out.append(current)
            current, size = [], 0
        current.append(seg)
        size += len(seg["text"])
    if current:
        out.append(current)
    return out


def page_span(segs: list[dict]) -> str:
    pages = [s["page"] for s in segs if s["page"]]
    if not pages:
        return ""
    lo, hi = min(pages), max(pages)
    approx = "≈ " if any(s["approx_page"] for s in segs) else ""
    return f"{approx}Page {lo}" if lo == hi else f"{approx}Pages {lo}–{hi}"


def language_rule(choice: str, sample: str) -> str:
    if choice == "ar":
        return "Write in Arabic."
    if choice == "en":
        return "Write in English."
    return "Write in Arabic." if len(AR_RE.findall(sample)) > len(sample) * 0.3 else "Write in English."


def text_of(segs: list[dict]) -> str:
    return "\n\n".join(f"({s['location']})\n{s['text']}" for s in segs)


# ------------------------------------------------------------------ 1. summaries
@task("summary")
def summarize(job: Job) -> dict:
    p = job.params
    segs = pick_segments(job.document_id, p)
    if not segs:
        raise ValueError("No text found in the selected pages.")
    detail = "a detailed summary with the key points, obligations, values and dates" if p.get("length") == "detailed" \
        else "a brief summary in 3 to 6 bullet points"
    lang = language_rule(p.get("language", "auto"), " ".join(s["text"] for s in segs[:20]))
    parts = groups(segs)
    partials = []
    for i, part in enumerate(parts, 1):
        job.progress((i - 1) / (len(parts) + 1), f"Reading part {i} of {len(parts)}")
        partials.append(llm.chat("fast", [
            {"role": "system", "content": f"Summarize this part of a document as {detail}. Keep figures and clause "
                                          f"numbers exactly as written. {lang} Use only the text given."},
            {"role": "user", "content": text_of(part)}], max_tokens=700,
            fake=lambda part=part: "• " + part[0]["text"][:160]))
    text = partials[0]
    if len(partials) > 1:
        job.progress(len(parts) / (len(parts) + 1), "Combining")
        while len(partials) > 1:  # combine in rounds so long documents stay within the model's context
            merged = []
            for k in range(0, len(partials), 6):
                chunk = partials[k:k + 6]
                merged.append(llm.chat("fast", [
                    {"role": "system", "content": f"Combine these partial summaries of one document into {detail}. "
                                                  f"Remove repetition. {lang}"},
                    {"role": "user", "content": "\n\n---\n\n".join(chunk)}], max_tokens=900,
                    fake=lambda chunk=chunk: "\n".join(chunk)))
            partials = merged
        text = partials[0]
    return {"text": text.strip(), "pages": page_span(segs), "scope": p.get("scope", "all"),
            "section": p.get("section_title", "")}


# ------------------------------------------------------------------ 4. outline (bookmarks)
def outline(document_id: str) -> list[dict]:
    """Volume > Article > Clause entries built from the document structure (and the PDF's own bookmarks)."""
    doc = db.get("documents", document_id)
    segs = segments_of(document_id)
    items: list[dict] = []
    seen = set()
    for seg in segs:
        for level, label in ((1, seg["location"].split(",")[0] if seg["location"].startswith("Volume") else ""),
                             (2, seg["article"]), (3, f"Clause {seg['clause']}" if seg["clause"] else "")):
            if not label:
                continue
            key = (level, label, seg["article"] if level == 3 else "")
            if key in seen:
                continue
            seen.add(key)
            title = seg["text"].split("\n")[0][:110]
            items.append({"level": level, "label": label, "title": title, "page": seg["page"],
                          "approx": bool(seg["approx_page"]), "segment_id": seg["id"]})
    # section ranges, so a section can be summarized on its own
    order = [s["id"] for s in segs]
    for i, item in enumerate(items):
        start = order.index(item["segment_id"])
        nxt = next((x for x in items[i + 1:] if x["level"] <= item["level"]), None)
        end = order.index(nxt["segment_id"]) if nxt else len(order)
        item["segment_ids"] = order[start:end]
    if doc and doc["kind"] == "pdf":
        try:
            with pymupdf.open(view_path(doc)) as pdf:
                toc = pdf.get_toc()
            if toc and len(items) < 3:  # the PDF's own bookmarks when no structure was detected
                items = [{"level": lvl, "label": title[:80], "title": "", "page": page, "approx": False,
                          "segment_id": next((s["id"] for s in segs if s["page"] == page), None), "segment_ids": []}
                         for lvl, title, page in toc]
        except Exception:
            pass
    return items


# ------------------------------------------------------------------ 5. whole-document translation
@task("translate")
def translate_document(job: Job) -> dict:
    import docx
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    from docx.oxml.ns import qn
    from docx.shared import Pt

    p = job.params
    segs = pick_segments(job.document_id, p)
    if not segs:
        raise ValueError("No text found in the selected pages.")
    sample = " ".join(s["text"] for s in segs[:30])
    source = "ar" if len(AR_RE.findall(sample)) > len(sample) * 0.3 else "en"
    target = p.get("target") or ("en" if source == "ar" else "ar")
    names = {"en": "English", "ar": "Arabic"}
    terms = translate.glossary()
    rule = ("Always use these fixed translations: " + "; ".join(f"{t['en']} = {t['ar']}" for t in terms[:60])) if terms else ""
    out: list[str] = []
    batches = [segs[i:i + 8] for i in range(0, len(segs), 8)]
    for i, batch in enumerate(batches, 1):
        job.progress((i - 1) / len(batches), f"Translating part {i} of {len(batches)}")
        raw = llm.chat("fast", [
            {"role": "system", "content": f"Translate each item from {names[source]} to {names[target]} for a legal or "
                                          f"technical reader. Keep numbers, clause references and names unchanged. {rule} "
                                          'Return only JSON: {"items": ["translation 1", "translation 2", ...]} in the same order.'},
            {"role": "user", "content": json.dumps([s["text"] for s in batch], ensure_ascii=False)}],
            json_mode=True, max_tokens=2500, temperature=0.1,
            fake=lambda batch=batch: json.dumps({"items": [f"[{target}] {s['text']}" for s in batch]}))
        items = llm.parse_json(raw, {}).get("items") if isinstance(llm.parse_json(raw, {}), dict) else None
        if not isinstance(items, list) or len(items) != len(batch):  # fall back to one item at a time
            items = [translate.translate(s["text"][:500], s["text"])["translation"] for s in batch]
        out += [str(x) for x in items]
    job.progress(0.97, "Writing the Word file")
    doc_row = db.get("documents", job.document_id)
    document = docx.Document()
    document.styles["Normal"].font.size = Pt(11)
    document.add_heading(f"{doc_row['filename']} — {names[target]} translation", level=1)
    table = document.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    for cell, label in zip(table.rows[0].cells, ("Location", names[source], names[target])):
        cell.text = label
    for seg, tr in zip(segs, out):
        row = table.add_row().cells
        row[0].text, row[1].text, row[2].text = seg["location"], seg["text"], tr
        for idx, lang in ((1, source), (2, target)):
            if lang == "ar":
                for par in row[idx].paragraphs:
                    par.alignment = WD_PARAGRAPH_ALIGNMENT.RIGHT
                    par._p.get_or_add_pPr().append(par._p.makeelement(qn("w:bidi"), {}))
    path = job.output(re.sub(r"\.[^.]+$", "", doc_row["filename"]) + f" - {names[target]}.docx")
    document.save(path)
    return {"_file": path, "count": len(out), "target": target, "pages": page_span(segs)}


# ------------------------------------------------------------------ 6a. mind map
@task("mindmap")
def mind_map(job: Job) -> dict:
    segs = pick_segments(job.document_id, job.params)
    sections = build_sections(segs)[:40]
    lang = language_rule(job.params.get("language", "auto"), " ".join(s["text"] for s in segs[:20]))
    by_id = {s["id"]: s for s in segs}
    children = []
    for i, sec in enumerate(sections, 1):
        job.progress((i - 1) / len(sections), f"Reading section {i} of {len(sections)}")
        part = [by_id[x] for x in sec["segment_ids"]]
        raw = llm.chat("fast", [
            {"role": "system", "content": f"List the 2 to 4 key points of this section, each under 12 words. {lang} "
                                          'Return only JSON: {"title": "short section title", "points": ["...", "..."]}'},
            {"role": "user", "content": text_of(part)[:GROUP_CHARS]}], json_mode=True, max_tokens=300,
            fake=lambda part=part: json.dumps({"title": part[0]["text"][:40], "points": [part[-1]["text"][:50]]}))
        data = llm.parse_json(raw, {})
        title = str(data.get("title") or sec["title"])[:80] if isinstance(data, dict) else sec["title"]
        points = [str(x)[:120] for x in (data.get("points") or [])][:4] if isinstance(data, dict) else []
        children.append({"title": title, "page": part[0]["page"], "segment_id": part[0]["id"],
                         "children": [{"title": pt} for pt in points]})
    doc_row = db.get("documents", job.document_id)
    return {"title": re.sub(r"\.[^.]+$", "", doc_row["filename"]), "children": children}


# ------------------------------------------------------------------ 6b. quiz
@task("quiz")
def quiz(job: Job) -> dict:
    count = max(3, min(20, int(job.params.get("count") or 8)))
    segs = pick_segments(job.document_id, job.params)
    sections = build_sections(segs)
    if not sections:
        raise ValueError("No text found for the quiz.")
    step = max(1, len(sections) // count)
    chosen = sections[::step][:count]
    lang = language_rule(job.params.get("language", "auto"), " ".join(s["text"] for s in segs[:20]))
    by_id = {s["id"]: s for s in segs}
    questions = []
    for i, sec in enumerate(chosen, 1):
        job.progress((i - 1) / len(chosen), f"Question {i} of {len(chosen)}")
        part = [by_id[x] for x in sec["segment_ids"]]
        raw = llm.chat("fast", [
            {"role": "system", "content": "Write one multiple-choice question that tests understanding of this text. "
                                          f"Exactly 4 options, one correct. {lang} Return only JSON: "
                                          '{"question": "...", "options": ["...", "...", "...", "..."], "answer": 0, '
                                          '"explanation": "why, citing the text"}'},
            {"role": "user", "content": text_of(part)[:GROUP_CHARS]}], json_mode=True, max_tokens=500,
            fake=lambda part=part: json.dumps({"question": f"What does {part[0]['location']} cover?",
                                               "options": [part[0]["text"][:60], "Something else", "Nothing", "Unknown"],
                                               "answer": 0, "explanation": "Stated in the text."}))
        data = llm.parse_json(raw, {})
        if isinstance(data, dict) and isinstance(data.get("options"), list) and len(data["options"]) >= 2:
            ans = int(data.get("answer") or 0)
            questions.append({"question": str(data.get("question", "")), "options": [str(o) for o in data["options"][:4]],
                              "answer": ans if 0 <= ans < len(data["options"][:4]) else 0,
                              "explanation": str(data.get("explanation", "")), "location": part[0]["location"],
                              "segment_id": part[0]["id"], "page": part[0]["page"]})
    return {"questions": questions}


# ------------------------------------------------------------------ 7. contract details and new draft
AMOUNT_RE = re.compile(r"(?:SAR|SR|USD|ريال)\s?\d[\d,]*(?:\.\d+)?|\d[\d,]{3,}(?:\.\d+)?\s?(?:SAR|SR|ريال)")
PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s?%")
DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{1,2}\s(?:January|February|March|April|May|June|July|August|"
                     r"September|October|November|December)\s\d{4}\b")
DURATION_RE = re.compile(r"\b\d+\s?(?:\(\w+\)\s?)?(?:calendar\s)?(?:days|weeks|months|years)\b", re.I)
REF_RE = re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]{2,}){2,}\b")
PLACEHOLDER_RE = re.compile(r"\[[^\]\n]{1,40}\]")


@task("contract_details")
def contract_details(job: Job) -> dict:
    """Find the details that change when a contract is reused for another contractor or offer."""
    segs = segments_of(job.document_id)
    found: dict[tuple, dict] = {}

    def add(kind: str, value: str, seg: dict) -> None:
        value = value.strip()
        if len(value) < 2:
            return
        key = (kind, normalize(value))
        if key not in found:
            found[key] = {"type": kind, "value": value, "count": 0, "locations": [], "segment_ids": []}
        item = found[key]
        item["count"] += 1
        if len(item["locations"]) < 5:
            item["locations"].append(seg["location"])
            item["segment_ids"].append(seg["id"])

    for seg in segs:  # rule-based: values, percentages, dates, durations, references, blanks
        for kind, rx in (("amount", AMOUNT_RE), ("percentage", PERCENT_RE), ("date", DATE_RE),
                         ("duration", DURATION_RE), ("reference", REF_RE), ("placeholder", PLACEHOLDER_RE)):
            for m in rx.finditer(seg["text"]):
                add(kind, m.group(0), seg)
    sections = build_sections(segs)
    by_id = {s["id"]: s for s in segs}
    for i, sec in enumerate(sections, 1):  # AI: party names, project, site, scope-specific items
        job.progress((i - 1) / max(1, len(sections)), f"Reading section {i} of {len(sections)}")
        part = [by_id[x] for x in sec["segment_ids"]]
        raw = llm.chat("fast", [
            {"role": "system", "content": "List the details in this contract text that would change if the contract were "
                                          "reused for a different contractor or offer: party names, project name, site, "
                                          "contract number, scope items. Copy each value exactly as written. Return only JSON: "
                                          '{"items": [{"type": "party|project|site|reference|scope|other", "value": "..."}]}'},
            {"role": "user", "content": text_of(part)[:GROUP_CHARS]}], json_mode=True, max_tokens=500,
            fake=lambda: json.dumps({"items": []}))
        data = llm.parse_json(raw, {})
        for item in (data.get("items") or []) if isinstance(data, dict) else []:
            value = str(item.get("value", "")).strip()
            seg = next((s for s in part if value and normalize(value) in normalize(s["text"])), None)
            if seg:  # keep only values that really appear in the text
                kind = str(item.get("type") or "other").lower()
                add(kind if kind in ("party", "project", "site", "reference", "scope") else "other", value, seg)
    order = {"party": 0, "project": 1, "reference": 2, "site": 3, "amount": 4, "percentage": 5, "duration": 6,
             "date": 7, "placeholder": 8, "scope": 9, "other": 10}
    items = sorted(found.values(), key=lambda x: (order.get(x["type"], 10), -x["count"]))
    return {"items": items[:300]}


def contract_draft(document_id: str, replacements: list[dict]) -> tuple[bytes, str]:
    """New Word draft with the given replacements. New values are highlighted yellow (to check);
    values left without a replacement are highlighted yellow as still pending."""
    import docx
    from docx.enum.text import WD_COLOR_INDEX

    from .ingest import original_path

    doc_row = db.get("documents", document_id)
    pairs = [(r["old"], r.get("new", "")) for r in replacements if r.get("old")]
    pairs.sort(key=lambda x: len(x[0]), reverse=True)

    def write(paragraph, text: str) -> None:
        """Rewrite a paragraph's text, highlighting replaced and pending values."""
        pattern = re.compile("|".join(re.escape(o) for o, _ in pairs)) if pairs else None
        lookup = dict(pairs)
        cursor = 0
        for m in (pattern.finditer(text) if pattern else []):
            if m.start() > cursor:
                paragraph.add_run(text[cursor:m.start()])
            new = lookup[m.group(0)]
            run = paragraph.add_run(new if new else m.group(0))
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            cursor = m.end()
        paragraph.add_run(text[cursor:])

    if doc_row["kind"] == "docx":  # keep the original layout; rewrite only paragraphs that change
        document = docx.Document(original_path(doc_row))
        paragraphs = list(document.paragraphs) + [p for t in document.tables for row in t.rows
                                                  for c in row.cells for p in c.paragraphs]
        for par in paragraphs:
            text = par.text
            if pairs and any(o in text for o, _ in pairs):
                first = par.runs[0] if par.runs else None
                style = (first.bold, first.italic, first.font.size, first.font.name) if first else None
                for run in list(par.runs):
                    run._r.getparent().remove(run._r)
                write(par, text)
                if style:
                    for run in par.runs:
                        run.bold, run.italic, run.font.size, run.font.name = style
    else:
        document = docx.Document()
        document.add_heading(f"New draft based on {doc_row['filename']}", level=1)
        for seg in segments_of(document_id):
            write(document.add_paragraph(), seg["text"])
    out = io.BytesIO()
    document.save(out)
    return out.getvalue(), re.sub(r"\.[^.]+$", "", doc_row["filename"]) + " - new draft.docx"


# ------------------------------------------------------------------ 2. tables to Excel
@task("tables")
def tables_to_excel(job: Job) -> dict:
    import openpyxl
    from openpyxl.styles import Font

    from .ingest import original_path

    doc_row = db.get("documents", job.document_id)
    book = openpyxl.Workbook()
    index = book.active
    index.title = "Index"
    index.append(["Sheet", "Location", "Rows", "Columns", "First row"])
    count = 0

    def number(value):
        text = str(value or "").strip()
        if re.fullmatch(r"-?[\d,]+(\.\d+)?", text) and any(ch.isdigit() for ch in text):
            try:
                return float(text.replace(",", "")) if "." in text else int(text.replace(",", ""))
            except ValueError:
                return text
        return text

    def add_table(rows: list[list], location: str) -> None:
        nonlocal count
        rows = [[number(c) for c in r] for r in rows if any(str(c or "").strip() for c in r)]
        if len(rows) < 2:
            return
        count += 1
        sheet = book.create_sheet(f"Table {count}")
        for r in rows:
            sheet.append(r)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        index.append([sheet.title, location, len(rows), max(len(r) for r in rows), " | ".join(str(c) for c in rows[0])[:120]])

    if doc_row["kind"] == "pdf":
        p = job.params
        with pymupdf.open(view_path(doc_row)) as pdf:
            start = int(p.get("start") or 1) if p.get("scope") in ("page", "range") else 1
            end = (int(p.get("end") or start) if p.get("scope") == "range" else start) if p.get("scope") in ("page", "range") \
                else len(pdf)
            for n in range(start, min(end, len(pdf)) + 1):
                job.progress((n - start) / max(1, end - start + 1), f"Page {n}")
                page = pdf[n - 1]
                for t in page.find_tables().tables:
                    # read each cell's text in reading order (the table extractor can scramble "1,250,000")
                    rows = [[" ".join(page.get_text("text", clip=pymupdf.Rect(cell)).split()) if cell else ""
                             for cell in row.cells] for row in t.rows]
                    add_table(rows, f"Page {n}")
    elif doc_row["kind"] == "docx":
        import docx
        for i, t in enumerate(docx.Document(original_path(doc_row)).tables, 1):
            add_table([[c.text for c in row.cells] for row in t.rows], f"Table {i} in the Word file")
    elif doc_row["kind"] == "xlsx":
        raise ValueError("This document is already an Excel file.")
    for cell in index[1]:
        cell.font = Font(bold=True)
    if not count:
        raise ValueError("No tables were found in the selected pages.")
    path = job.output(re.sub(r"\.[^.]+$", "", doc_row["filename"]) + " - tables.xlsx")
    book.save(path)
    return {"_file": path, "tables": count}


# ------------------------------------------------------------------ 3. redaction and sanitizing
def redact_search(document_id: str, term: str) -> int:
    """Mark every occurrence of a word or phrase for redaction (PDF)."""
    doc_row = db.get("documents", document_id)
    if doc_row["kind"] != "pdf" or not term.strip():
        return 0
    from .config import load_settings
    added = 0
    with pymupdf.open(view_path(doc_row)) as pdf:
        for page in pdf:
            w, h = page.rect.width, page.rect.height
            hits = page.search_for(term.strip())
            if hits:
                db.insert("notes", {"workspace_id": doc_row["workspace_id"], "document_id": document_id, "mark": "redact",
                                    "page": page.number + 1, "quote": term.strip(), "body": "", "status": "open",
                                    "rects": [[r.x0 / w, r.y0 / h, (r.x1 - r.x0) / w, (r.y1 - r.y0) / h] for r in hits],
                                    "author": load_settings().get("author", "You"), "location": f"Page {page.number + 1}",
                                    "created": db.now(), "updated": db.now()})
                added += len(hits)
    return added


def redacted_pdf(document_id: str) -> tuple[bytes, str]:
    """A copy with marked areas removed for good (text and images) and hidden data cleaned out."""
    doc_row = db.get("documents", document_id)
    if doc_row["kind"] != "pdf":
        raise ValueError("Redaction is available for PDF documents.")
    marks = db.query("SELECT * FROM notes WHERE document_id = ? AND mark = 'redact'", (document_id,))
    with pymupdf.open(view_path(doc_row)) as pdf:
        for m in marks:
            page = pdf[m["page"] - 1]
            w, h = page.rect.width, page.rect.height
            for r in m["rects"] or []:
                page.add_redact_annot(pymupdf.Rect(r[0] * w, r[1] * h, (r[0] + r[2]) * w, (r[1] + r[3]) * h), fill=(0, 0, 0))
        for page in pdf:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS)
        pdf.scrub(attached_files=True, clean_pages=True, embedded_files=True, hidden_text=True, javascript=True,
                  metadata=True, redactions=False, remove_links=False, reset_fields=False, reset_responses=True,
                  thumbnails=True, xml_metadata=True)
        data = pdf.tobytes(garbage=4, deflate=True)
    return data, re.sub(r"\.[^.]+$", "", doc_row["filename"]) + " - redacted.pdf"


# ------------------------------------------------------------------ 8. save notes into the PDF
COLORS = {"yellow": (1, 0.9, 0.54), "green": (0.74, 0.92, 0.78), "blue": (0.75, 0.87, 0.98),
          "pink": (0.98, 0.79, 0.87), "orange": (1, 0.79, 0.6)}


def _rgb(hex_color: str) -> tuple:
    hex_color = (hex_color or "#17202B").lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def annotated_pdf(document_id: str, include_findings: bool = True) -> tuple[bytes, str]:
    """The PDF with highlights, underlines, notes, ink and handwriting saved as standard PDF annotations."""
    doc_row = db.get("documents", document_id)
    if doc_row["kind"] != "pdf":
        raise ValueError("Saving notes into the file is available for PDF documents. Use Export to Word for others.")
    notes = db.query("SELECT * FROM notes WHERE document_id = ? AND (mark IS NULL OR mark != 'redact') "
                     "ORDER BY page, created", (document_id,))
    segs = {s["id"]: s for s in segments_of(document_id)}
    with pymupdf.open(view_path(doc_row)) as pdf:
        for n in notes:
            if not n["page"] or n["page"] > len(pdf):
                continue
            page = pdf[n["page"] - 1]
            w, h = page.rect.width, page.rect.height
            label = " · ".join(x for x in [(n["category"] or "").capitalize(), (n["status"] or "").capitalize()] if x)
            content = "\n".join(x for x in [label, n["body"] or "", n["location"] or ""] if x)
            rects = [pymupdf.Rect(r[0] * w, r[1] * h, (r[0] + r[2]) * w, (r[1] + r[3]) * h) for r in (n["rects"] or [])]
            if not rects and n["mark"] in ("highlight", "underline") and n["quote"]:
                rects = page.search_for(n["quote"][:200])  # marks made in the text view: find the words on the page
            annot = None
            if n["mark"] in ("highlight", "underline") and rects:
                annot = (page.add_highlight_annot if n["mark"] == "highlight" else page.add_underline_annot)(rects)
                annot.set_colors(stroke=COLORS.get(n["color"] or "yellow", COLORS["yellow"]))
            elif n["mark"] == "ink" and n["ink"]:
                annot = page.add_ink_annot([[(p[0] * w, p[1] * h) for p in s["points"]] for s in n["ink"]])
                annot.set_colors(stroke=_rgb(n["ink"][0].get("color")))
                annot.set_border(width=n["ink"][0].get("width", 2))
            if annot:
                annot.set_info(content=content, title=n["author"] or "GrepLens")
                annot.update()
            anchor_y = rects[0].y0 if rects else ((segs.get(n["segment_id"]) or {}).get("bbox") or [0, 0.08])[1] * h
            if (n["body"] and not annot) or n.get("handwriting"):  # a note icon in the right margin
                hw_text = f"Handwriting reads: {n['handwriting_text']}" if n.get("handwriting_text") else ""
                icon = page.add_text_annot(pymupdf.Point(w - 28, max(10, anchor_y)),
                                           "\n".join(x for x in (content, hw_text) if x) or "Handwritten note", icon="Note")
                icon.set_info(title=n["author"] or "GrepLens")
                icon.update()
            if n.get("handwriting"):  # handwriting drawn small in the right margin, as ink
                strokes = n["handwriting"]
                box_w, x0, y0 = 110, w - 140, max(10, anchor_y + 14)
                ink = page.add_ink_annot([[(x0 + p[0] * box_w, y0 + p[1] * box_w) for p in s["points"]] for s in strokes])
                ink.set_colors(stroke=_rgb(strokes[0].get("color")))
                ink.set_border(width=1.2)
                ink.set_info(content=n.get("handwriting_text") or "Handwritten note", title=n["author"] or "GrepLens")
                ink.update()
        if include_findings:
            review = db.query("SELECT id FROM reviews WHERE document_id = ? ORDER BY created DESC LIMIT 1", (document_id,))
            for f in db.query("SELECT * FROM findings WHERE review_id = ?", (review[0]["id"],)) if review else []:
                seg = segs.get(f["segment_id"])
                if not f["page"] or f["page"] > len(pdf):
                    continue
                page = pdf[f["page"] - 1]
                y = (seg["bbox"][1] if seg and seg.get("bbox") else 0.1) * page.rect.height
                a = page.add_text_annot(pymupdf.Point(page.rect.width - 46, max(10, y)),
                                        f"[{f['severity'].capitalize()} · {f['category']}] {f['title']}\n"
                                        f"Recommendation: {f['recommendation']}", icon="Comment")
                a.set_info(title="GrepLens Review")
                a.set_colors(stroke=(0.85, 0.18, 0.13))
                a.update()
        data = pdf.tobytes(garbage=3, deflate=True)
    return data, re.sub(r"\.[^.]+$", "", doc_row["filename"]) + " - with notes.pdf"


def output_file(job: dict) -> Path | None:
    return Path(job["file"]) if job and job.get("file") else None
