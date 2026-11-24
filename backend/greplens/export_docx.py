"""Export highlights, underlines, notes and review findings to Word (.docx) as comments.

- Word source: the original file is kept and each comment is anchored on the paragraph that holds the
  quoted text, so the original formatting stays intact.
- Other sources (PDF, Excel, text): a Word copy of the text is built in reading order, and each comment
  is anchored on the exact quoted words.
"""
from __future__ import annotations

import io
import re

import docx
from docx.enum.text import WD_COLOR_INDEX
from docx.shared import Pt

from . import db
from .ingest import original_path, segments_of
from .review import locate, normalize

HIGHLIGHT = {"yellow": WD_COLOR_INDEX.YELLOW, "green": WD_COLOR_INDEX.BRIGHT_GREEN, "blue": WD_COLOR_INDEX.TURQUOISE,
             "pink": WD_COLOR_INDEX.PINK, "orange": WD_COLOR_INDEX.YELLOW}


def _items(document_id: str) -> list[dict]:
    items = []
    for n in db.query("SELECT * FROM notes WHERE document_id = ? AND (mark IS NULL OR mark != 'ink') "
                      "ORDER BY page, created", (document_id,)):
        label = " · ".join(x for x in [(n["category"] or "Note").capitalize(), (n["status"] or "open").capitalize(),
                                        " ".join(f"#{t}" for t in (n["tags"] or []))] if x)
        body = f"[{label}] {n['body'] or ''}".strip()
        if n["ai_answer"]:
            body += " (saved AI answer)"
        items.append({"quote": n["quote"] or "", "text": body, "author": n["author"] or "GrepLens",
                      "segment_id": n["segment_id"], "mark": n["mark"], "color": n["color"]})
    review = db.query("SELECT id FROM reviews WHERE document_id = ? ORDER BY created DESC LIMIT 1", (document_id,))
    if review:
        for f in db.query("SELECT * FROM findings WHERE review_id = ?", (review[0]["id"],)):
            items.append({"quote": f["quote"], "author": "GrepLens Review", "segment_id": f["segment_id"],
                          "text": f"[{f['severity'].capitalize()} · {f['category']}] {f['title']}. "
                                  f"Recommendation: {f['recommendation']} ({f['location']})", "mark": None, "color": None})
    return items


def export(document_id: str) -> tuple[bytes, str]:
    doc = db.get("documents", document_id)
    items = _items(document_id)
    if doc["kind"] == "docx":
        data = _annotate_word(original_path(doc), items)
    else:
        data = _build_copy(doc, items)
    name = re.sub(r"\.[^.]+$", "", doc["filename"]) + " - GrepLens notes.docx"
    return data, name


def _annotate_word(path, items) -> bytes:
    document = docx.Document(path)
    paragraphs = [p for p in document.paragraphs if p.text.strip()]
    for cell_par in (p for t in document.tables for row in t.rows for c in row.cells for p in c.paragraphs):
        if cell_par.text.strip():
            paragraphs.append(cell_par)
    for item in items:
        q = normalize(item["quote"])
        target = next((p for p in paragraphs if q and q in normalize(p.text)), None)
        if target is None or not target.runs:
            continue
        text = f"“{item['quote'][:120]}” — {item['text']}" if item["quote"] else item["text"]
        document.add_comment(target.runs, text=text, author=item["author"], initials=item["author"][:2].upper())
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _build_copy(doc: dict, items: list[dict]) -> bytes:
    document = docx.Document()
    style = document.styles["Normal"]
    style.font.name, style.font.size = "Calibri", Pt(11)
    style.paragraph_format.space_after = Pt(6)
    document.add_heading(doc["filename"], level=1)
    document.add_paragraph("Text copy with GrepLens notes and review findings as comments. Page numbers refer "
                           "to the original document.").italic = True
    segments = segments_of(doc["id"])
    by_segment: dict[str, list[dict]] = {}
    for item in items:
        seg = next((s for s in segments if s["id"] == item["segment_id"]), None) or locate(item["quote"], segments)
        if seg:
            by_segment.setdefault(seg["id"], []).append(item)
    inks = _ink_images(doc) if doc["kind"] == "pdf" else {}
    last_page = None
    for seg in segments:
        if seg["page"] and seg["page"] != last_page and doc["kind"] == "pdf":
            if last_page is not None:
                _write_inks(document, inks.pop(last_page, []))
            document.add_paragraph().add_run(f"— Page {seg['page']} —").italic = True
            last_page = seg["page"]
        paragraph = document.add_paragraph()
        _write_with_comments(document, paragraph, seg["text"], by_segment.get(seg["id"], []))
    for page in sorted(inks):
        _write_inks(document, inks[page])
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _write_with_comments(document, paragraph, text: str, items: list[dict]) -> None:
    """Split the paragraph so each quoted phrase is its own run carrying the comment."""
    spans = []
    lower = text.lower()
    for item in items:
        q = item["quote"].strip()
        pos = lower.find(q.lower()) if q else -1
        spans.append((pos, pos + len(q), item) if pos >= 0 else (-1, -1, item))
    exact = sorted([s for s in spans if s[0] >= 0], key=lambda s: s[0])
    cursor, used = 0, []
    for start, end, item in exact:
        if start < cursor:  # overlapping quotes: attach to the whole paragraph instead
            used.append(item)
            continue
        if start > cursor:
            paragraph.add_run(text[cursor:start])
        run = paragraph.add_run(text[start:end])
        if item["mark"] == "underline":
            run.underline = True
        elif item["mark"] == "highlight":
            run.font.highlight_color = HIGHLIGHT.get(item["color"] or "yellow", WD_COLOR_INDEX.YELLOW)
        document.add_comment(run, text=item["text"], author=item["author"], initials=item["author"][:2].upper())
        cursor = end
    if cursor < len(text) or not paragraph.runs:
        paragraph.add_run(text[cursor:])
    for item in [s[2] for s in spans if s[0] < 0] + used:
        document.add_comment(paragraph.runs, text=f"“{item['quote'][:120]}” — {item['text']}",
                             author=item["author"], initials=item["author"][:2].upper())


def _ink_images(doc: dict) -> dict[int, list[tuple[bytes, dict]]]:
    """Render each freehand ink note over its page area as a PNG, grouped by page."""
    import pymupdf

    from .ingest import view_path

    notes = db.query("SELECT * FROM notes WHERE document_id = ? AND mark = 'ink' ORDER BY page, created", (doc["id"],))
    out: dict[int, list[tuple[bytes, dict]]] = {}
    if not notes:
        return out
    with pymupdf.open(view_path(doc)) as pdf:
        for note in notes:
            if not note["page"] or note["page"] > len(pdf) or not note["ink"]:
                continue
            page = pdf[note["page"] - 1]
            w, h = page.rect.width, page.rect.height
            xs = [p[0] * w for s in note["ink"] for p in s["points"]]
            ys = [p[1] * h for s in note["ink"] for p in s["points"]]
            if not xs:
                continue
            shape = page.new_shape()
            for stroke in note["ink"]:
                pts = [pymupdf.Point(p[0] * w, p[1] * h) for p in stroke["points"]]
                if len(pts) > 1:
                    shape.draw_polyline(pts)
                    shape.finish(color=_rgb(stroke.get("color", "#D92D20")), width=stroke.get("width", 2), closePath=False,
                                 lineCap=1, lineJoin=1)
            shape.commit()
            clip = pymupdf.Rect(min(xs) - 40, min(ys) - 30, max(xs) + 40, max(ys) + 30) & page.rect
            out.setdefault(note["page"], []).append((page.get_pixmap(clip=clip, dpi=110).tobytes("png"), note))
    return out


def _rgb(hex_color: str) -> tuple[float, float, float]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4)) if len(hex_color) == 6 else (0.85, 0.18, 0.13)


def _write_inks(document, items: list[tuple[bytes, dict]]) -> None:
    from docx.shared import Inches

    for png, note in items:
        run = document.add_paragraph().add_run()
        run.add_picture(io.BytesIO(png), width=Inches(4))
        label = " · ".join(x for x in [(note["category"] or "Ink note").capitalize(), (note["status"] or "open").capitalize()] if x)
        document.add_comment(run, text=f"[{label}] {note['body'] or 'Handwritten mark'} ({note['location'] or ''})".strip(),
                             author=note["author"] or "GrepLens", initials=(note["author"] or "GL")[:2].upper())
