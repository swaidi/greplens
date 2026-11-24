"""Text extraction with exact locations (volume, article, clause, paragraph, page)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

SUPPORTED = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".xlsm": "xlsx", ".pptx": "pptx",
             ".txt": "text", ".md": "text", ".csv": "text"}
CHARS_PER_PAGE = 3000  # Word files have no fixed pages; used for the approximate page number

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
VOLUME_RE = re.compile(r"^\s*(?:VOLUME|Volume|المجلد)\s*[-:]?\s*([0-9IVX٠-٩]+)")
ARTICLE_RE = re.compile(r"^\s*(?:ARTICLE|Article|SECTION|Section|APPENDIX|Appendix|المادة|البند|الملحق)"
                        r"\s*[-:(]?\s*([0-9A-Z٠-٩]+)")
CLAUSE_RE = re.compile(r"^\s*\(?([0-9٠-٩]+(?:\.[0-9٠-٩]+)+)\)?[\s.:)-]")


@dataclass
class Segment:
    text: str
    page: int = 0                 # 1-based; exact for PDF, approximate for other formats
    approx_page: bool = False
    volume: str = ""
    article: str = ""
    clause: str = ""
    paragraph: int = 0
    bbox: list[float] = field(default_factory=list)   # normalized [x0, y0, x1, y1] on the page
    sheet: str = ""
    row: int = 0

    @property
    def location(self) -> str:
        parts = []
        if self.volume:
            parts.append(f"Volume {self.volume}")
        if self.sheet:
            parts.append(f"Sheet '{self.sheet}', Row {self.row}")
        if self.article:
            parts.append(self.article)
        if self.clause:
            parts.append(f"Clause {self.clause}")
        if self.paragraph and not self.sheet:
            parts.append(f"Paragraph {self.paragraph}")
        if self.page:
            parts.append(f"{'≈ ' if self.approx_page else ''}Page {self.page}")
        return ", ".join(parts)


class _Tracker:
    """Keeps the current volume/article/clause while walking the document in order."""

    def __init__(self):
        self.volume = self.article = self.clause = ""
        self.paragraph = 0

    def place(self, seg: Segment) -> Segment:
        first_line = seg.text.strip().split("\n", 1)[0][:160]
        if m := VOLUME_RE.match(first_line):
            self.volume = m.group(1).translate(_AR_DIGITS)
        if m := ARTICLE_RE.match(first_line):
            word = first_line.strip().split()[0].rstrip(":-(").capitalize()
            label = {"المادة": "Article", "البند": "Clause", "الملحق": "Appendix"}.get(word, word)
            self.article = f"{label} {m.group(1).translate(_AR_DIGITS)}"
            self.clause, self.paragraph = "", 0
        if m := CLAUSE_RE.match(first_line):
            self.clause = m.group(1).translate(_AR_DIGITS)
            self.paragraph = 0
        self.paragraph += 1
        seg.volume, seg.article, seg.clause, seg.paragraph = self.volume, self.article, self.clause, self.paragraph
        return seg


def kind_of(filename: str) -> str | None:
    return SUPPORTED.get(Path(filename).suffix.lower())


def extract(path: Path, kind: str) -> tuple[list[Segment], int]:
    """Return (segments in reading order, page count)."""
    return {"pdf": _pdf, "docx": _docx, "xlsx": _xlsx, "pptx": _pptx, "text": _text}[kind](path)


def _pdf(path: Path):
    tracker, out = _Tracker(), []
    with pymupdf.open(path) as doc:
        for number, page in enumerate(doc, 1):
            w, h = page.rect.width or 1, page.rect.height or 1
            for x0, y0, x1, y1, text, _no, btype in page.get_text("blocks", sort=True):
                text = re.sub(r"[ \t]+", " ", text).strip()
                if btype != 0 or len(text) < 2:
                    continue
                out.append(tracker.place(Segment(text=text, page=number,
                                                 bbox=[x0 / w, y0 / h, x1 / w, y1 / h])))
        return out, len(doc)


def _docx(path: Path):
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(path)
    tracker, out, chars, page = _Tracker(), [], 0, 1
    has_rendered_breaks = any(p.rendered_page_breaks for p in document.paragraphs[:400])
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            if has_rendered_breaks:
                page += len(block.rendered_page_breaks)
            text = block.text.strip()
        elif isinstance(block, Table):
            rows = [" | ".join(c.text.strip() for c in row.cells) for row in block.rows]
            text = "\n".join(r for r in rows if r.strip(" |"))
        else:
            continue
        if not text:
            continue
        chars += len(text)
        if not has_rendered_breaks:
            page = 1 + chars // CHARS_PER_PAGE
        out.append(tracker.place(Segment(text=text, page=page, approx_page=True)))
    return out, page


def _xlsx(path: Path):
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for sheet in book.worksheets:
        for index, row in enumerate(sheet.iter_rows(values_only=True), 1):
            cells = [str(v).strip() for v in row if v is not None and str(v).strip()]
            if cells:
                out.append(Segment(text=" | ".join(cells), sheet=sheet.title, row=index))
    book.close()
    return out, len(book.sheetnames)


def _pptx(path: Path):
    from pptx import Presentation

    tracker, out = _Tracker(), []
    deck = Presentation(path)
    for number, slide in enumerate(deck.slides, 1):
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                out.append(tracker.place(Segment(text=shape.text_frame.text.strip(), page=number)))
    return out, len(deck.slides)


def _text(path: Path):
    raw = path.read_text(encoding="utf-8", errors="replace")
    tracker, out, chars = _Tracker(), [], 0
    for block in re.split(r"\n\s*\n", raw):
        if block.strip():
            chars += len(block)
            out.append(tracker.place(Segment(text=block.strip(), page=1 + chars // CHARS_PER_PAGE, approx_page=True)))
    return out, max(1, chars // CHARS_PER_PAGE + 1)
