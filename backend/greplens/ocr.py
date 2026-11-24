"""OCR for scanned PDF pages, ported from GrepAll (same author, AGPL-3.0).

Only pages without a usable text layer are processed. Each original page is kept unchanged and
an invisible text layer is added, so the viewer still shows the scan while text can be selected,
searched and indexed. Uses the Tesseract engine built into PyMuPDF; only language data
(eng/ara .traineddata) is needed.
"""
from __future__ import annotations

import math
import os
import re
import sys
from pathlib import Path

import pymupdf

from .config import resources_dir

MIN_TEXT_CHARS = 25
MAX_RENDER_PIXELS = 60_000_000
OCR_DPI = 300
OCR_LANG = "eng+ara"

_COMMON_WORDS = frozenset("""
the and of to in for with on at by from is are be this that as or an no not it its we our your you all any
date ref page total contract contractor works project shall will per item qty quantity rate amount price sar
subject dear sir regards letter notice delay supply materials site order purchase details number description
unit value tax vat invoice payment period days within accordance agreement scope work approved approval
""".split()) | frozenset("""
في من على إلى الى أن ان عن مع هذا هذه التي الذي رقم تاريخ بتاريخ المشروع العقد المقاول السعر الكمية ريال
الموقع خلال يوم أمر امر شركة المواد جميع بتوريد يلتزم المورد وذلك او أو قبل بعد كما بين حسب لا ما تم وفق
المبلغ القيمة الضريبة الدفع الأعمال الاعمال البند الوحدة الإجمالي الاجمالي المملكة العربية السعودية
""".split())


def find_tessdata() -> str | None:
    candidates = [os.environ.get("TESSDATA_PREFIX", ""), str(resources_dir() / "tessdata"),
                  str(Path(sys.executable).parent / "tessdata")]
    try:
        candidates.append(pymupdf.get_tessdata() or "")
    except Exception:
        pass
    candidates += ["/usr/share/tesseract-ocr/5/tessdata", "/usr/share/tesseract-ocr/4.00/tessdata",
                   "/usr/share/tessdata", "/usr/local/share/tessdata",
                   r"C:\Program Files\Tesseract-OCR\tessdata",
                   str(Path.home() / "AppData/Local/Programs/Tesseract-OCR/tessdata")]
    for folder in candidates:
        path = Path(folder) if folder else None
        if path and path.is_dir() and all((path / f"{lang}.traineddata").is_file() for lang in OCR_LANG.split("+")):
            return str(path)
    return None


def available() -> bool:
    return find_tessdata() is not None


def needs_ocr(page) -> bool:
    return len(page.get_text("text").strip()) < MIN_TEXT_CHARS


def _reading_score(text: str) -> int:
    return sum(1 for word in re.findall(r"[A-Za-z\u0600-\u06FF]+", text.lower()) if word in _COMMON_WORDS)


def _safe_dpi(page, dpi: int) -> int:
    pixels = page.rect.width * page.rect.height * (dpi / 72) ** 2
    if pixels <= MAX_RENDER_PIXELS:
        return dpi
    return max(72, int(72 * math.sqrt(MAX_RENDER_PIXELS / (page.rect.width * page.rect.height))))


def _ocr_bytes(page, angle: int, dpi: int, tessdata: str) -> bytes:
    matrix = pymupdf.Matrix(dpi / 72, dpi / 72).prerotate(angle)
    pixmap = page.get_pixmap(matrix=matrix)
    pixmap.set_dpi(dpi, dpi)
    return pixmap.pdfocr_tobytes(compress=True, language=OCR_LANG, tessdata=tessdata)


def _strip_images(page) -> None:
    doc = page.parent
    page.clean_contents()
    for image in page.get_images(full=True):
        name, referencer = image[7], image[9]
        stream_xref = referencer or page.get_contents()[0]
        stream = doc.xref_stream(stream_xref)
        updated, count = re.subn(rb"/" + re.escape(name.encode()) + rb"\s+Do\b", b"", stream)
        if count:
            doc.update_stream(stream_xref, updated)
        doc.xref_set_key(referencer or page.xref, f"Resources/XObject/{name}", "null")


def _detect_angle(page, first_text: str, tessdata: str) -> int:
    base = _reading_score(first_text)
    if base >= 3:
        return 0
    best_angle, best_score = 0, base
    for angle in (90, 270, 180):
        with pymupdf.open("pdf", _ocr_bytes(page, angle, 110, tessdata)) as trial:
            score = _reading_score(trial[0].get_text())
        if score > best_score:
            best_angle, best_score = angle, score
    return best_angle if best_score >= max(3, 2 * base + 1) else 0


def ocr_pdf(src: Path, dst: Path, progress=None) -> int:
    """Write dst with a text layer on every scanned page. Returns how many pages were OCR'd."""
    tessdata = find_tessdata()
    if not tessdata:
        raise FileNotFoundError("OCR language data (eng, ara) was not found.")
    doc = pymupdf.open(src)
    output = pymupdf.open()
    count = 0
    try:
        total = len(doc)
        for number in range(total):
            output.insert_pdf(doc, from_page=number, to_page=number)
            if needs_ocr(doc[number]):
                target = output[-1]
                rotation = target.rotation
                target.set_rotation(0)
                dpi = _safe_dpi(target, OCR_DPI)
                data = _ocr_bytes(target, 0, dpi, tessdata)
                with pymupdf.open("pdf", data) as first:
                    angle = _detect_angle(target, first[0].get_text(), tessdata)
                if angle:
                    data = _ocr_bytes(target, angle, dpi, tessdata)
                with pymupdf.open("pdf", data) as ocr_doc:
                    _strip_images(ocr_doc[0])
                    target.show_pdf_page(target.rect, ocr_doc, 0, overlay=True, rotate=-angle)
                target.set_rotation(angle or rotation)
                count += 1
            if progress:
                progress(number + 1, total)
        tmp = dst.with_name(dst.stem + ".partial.pdf")
        output.save(tmp, garbage=4, deflate=True)
        output.close()
        os.replace(tmp, dst)
    finally:
        if not output.is_closed:
            output.close()
        doc.close()
    return count
