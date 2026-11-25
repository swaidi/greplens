"""Formatting checks read from the Word (.docx) file itself, not from the extracted text:

- numbers typed as text where automatic numbering is expected
- empty paragraphs used for spacing instead of "space after"
- clauses at the same level with different indentation
- tables wider than the text area (past the right margin)

Page layout rules (for example where an article breaks across pages) need the rendered pages and are
not checked here.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from .review import normalize

TYPED_NUMBER_RE = re.compile(r"^\s*(?:\(?\d{1,3}(?:\.\d{1,3})+\)?[.)]?|\d{1,3}[.)]|\([a-z]\)|[a-z]\)|\((?:i|ii|iii|iv|v|vi|vii|viii|ix|x)\))\s+\S")
MAX_EXAMPLES = 6
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _has_numbering(paragraph) -> bool:
    ppr = paragraph._p.pPr
    if ppr is not None and ppr.numPr is not None:
        return True
    style = paragraph.style
    while style is not None:
        sppr = style.element.pPr
        if sppr is not None and sppr.numPr is not None:
            return True
        style = style.base_style
    return False


def _level(paragraph) -> int:
    ppr = paragraph._p.pPr
    if ppr is not None and ppr.numPr is not None and ppr.numPr.ilvl is not None:
        return int(ppr.numPr.ilvl.val)
    return 0


def _is_empty(paragraph) -> bool:
    if paragraph.text.strip():
        return False
    xml = paragraph._p.xml
    return not any(tag in xml for tag in ("<w:br w:type=\"page\"", "<w:drawing", "<w:pict", "<w:sectPr", "<w:object"))


class _Locator:
    """Find the extracted segment (with its exact location) that holds a paragraph's text."""

    def __init__(self, segments: list[dict]):
        self.segments = segments
        self.norm = [normalize(s["text"]) for s in segments]
        self.pos = 0

    def find(self, text: str) -> dict | None:
        key = normalize(text)[:60]
        if len(key) < 3:
            return None
        order = list(range(self.pos, len(self.norm))) + list(range(0, self.pos))
        for i in order:
            if key in self.norm[i]:
                self.pos = i
                return self.segments[i]
        return None


def check(path: Path, segments: list[dict]) -> list[dict]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(path)
    locate = _Locator(segments)
    findings: list[dict] = []
    blocks = list(document.iter_inner_content())
    paragraphs = [b for b in blocks if isinstance(b, Paragraph)]

    # 1. typed numbers
    typed = [p for p in paragraphs if TYPED_NUMBER_RE.match(p.text) and not _has_numbering(p)]
    for p in typed[:MAX_EXAMPLES]:
        number = TYPED_NUMBER_RE.match(p.text).group(0).strip().split()[0]
        findings.append({"severity": "low", "title": "Number typed as text",
                         "quote": p.text.strip()[:120], "seg": locate.find(p.text),
                         "recommendation": f"Remove the typed \"{number}\" and apply automatic numbering, so numbers and "
                                           "cross-references update by themselves."})
    if len(typed) > MAX_EXAMPLES:
        findings.append({"severity": "low", "title": f"{len(typed)} paragraphs use typed numbers instead of automatic numbering",
                         "quote": "", "seg": None, "recommendation": "The first examples are listed separately."})

    # 2. empty paragraphs used for spacing (two or more in a row)
    runs, count = [], 0
    for i, p in enumerate(paragraphs):
        if _is_empty(p):
            count += 1
            continue
        if count >= 2:
            runs.append((count, p))
        count = 0
    for n, after in runs[:MAX_EXAMPLES]:
        findings.append({"severity": "low", "title": f"{n} empty lines used for spacing",
                         "quote": after.text.strip()[:120], "seg": locate.find(after.text),
                         "recommendation": "Delete the empty lines before this paragraph and use the paragraph's "
                                           "\"space after\" setting (about 6 pt) instead."})
    if len(runs) > MAX_EXAMPLES:
        findings.append({"severity": "low", "title": f"Empty lines are used for spacing in {len(runs)} places",
                         "quote": "", "seg": None, "recommendation": "Use \"space after\" instead of empty paragraphs."})

    # 3. same-level clauses with different indentation (direct formatting)
    groups: dict[tuple, list] = defaultdict(list)
    for p in paragraphs:
        if not p.text.strip():
            continue
        numbered = _has_numbering(p)
        if not numbered and not TYPED_NUMBER_RE.match(p.text):
            continue
        fmt = p.paragraph_format
        indent = (round((fmt.left_indent or 0) / 12700), round((fmt.first_line_indent or 0) / 12700))  # points
        groups[(p.style.name if p.style is not None else "", _level(p), numbered)].append((indent, p))
    for items in groups.values():
        if len(items) < 4:
            continue
        common, freq = Counter(i for i, _ in items).most_common(1)[0]
        if freq < len(items) * 0.6:
            continue
        odd = [(i, p) for i, p in items if i != common]
        for indent, p in odd[:MAX_EXAMPLES]:
            findings.append({"severity": "low", "title": "Indentation differs from other clauses at the same level",
                             "quote": p.text.strip()[:120], "seg": locate.find(p.text),
                             "recommendation": f"Use the same indentation as the other clauses at this level "
                                               f"(left {common[0]} pt, first line {common[1]} pt; this one is "
                                               f"left {indent[0]} pt, first line {indent[1]} pt)."})

    # 4. tables past the right margin
    section = document.sections[0]
    text_width = None
    if section.page_width and section.left_margin is not None and section.right_margin is not None:
        text_width = (section.page_width - section.left_margin - section.right_margin) / 635  # twips
    for block in blocks:
        if not isinstance(block, Table) or not text_width:
            continue
        grid = block._tbl.tblGrid
        width = sum(int(col.get(f"{W}w") or 0) for col in grid.findall(f"{W}gridCol")) if grid is not None else 0
        ind = block._tbl.tblPr.find(f"{W}tblInd") if block._tbl.tblPr is not None else None
        indent = int(ind.get(f"{W}w") or 0) if ind is not None and (ind.get(f"{W}type") in (None, "dxa")) else 0
        if width and indent + width > text_width * 1.01:
            first = next((c.text for row in block.rows for c in row.cells if c.text.strip()), "")
            over = round((indent + width - text_width) / 20, 1)
            findings.append({"severity": "low", "title": "Table extends past the right margin",
                             "quote": first.strip()[:120], "seg": locate.find(first),
                             "recommendation": f"Reduce the table width by about {over} pt, aligned with the clause text, "
                                               "so it stays inside the margins."})
    return findings
