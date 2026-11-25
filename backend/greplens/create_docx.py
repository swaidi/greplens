"""Word (.docx) export for the Create panel outputs. Each point carries its sources (document, location)."""
from __future__ import annotations

import re
from pathlib import Path

import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

AR_RE = re.compile(r"[؀-ۿ]")
GRAY = RGBColor(0x55, 0x60, 0x70)


def _sources(cites: list[dict]) -> str:
    return "; ".join(f"{c['document']}, {c['location']}" for c in cites or [])


def _rtl(paragraph, text: str) -> None:
    if len(AR_RE.findall(text)) > len(text) * 0.3:
        ppr = paragraph._p.get_or_add_pPr()
        bidi = ppr.makeelement(qn("w:bidi"), {})
        ppr.append(bidi)


def _para(doc, text: str, style: str | None = None, bold: bool = False):
    p = doc.add_paragraph(style=style)
    run = p.add_run(text)
    run.bold = bold
    _rtl(p, text)
    p.paragraph_format.space_after = Pt(6)
    return p


def _cite(doc, cites: list[dict]) -> None:
    if not cites:
        return
    p = doc.add_paragraph()
    run = p.add_run("Source: " + _sources(cites))
    run.font.size = Pt(8.5)
    run.font.color.rgb = GRAY
    p.paragraph_format.space_after = Pt(6)


def _point(doc, text: str, cites: list[dict]) -> None:
    p = doc.add_paragraph(style="List Bullet")
    p.add_run(text)
    _rtl(p, text)
    if cites:
        run = p.add_run("  [" + _sources(cites) + "]")
        run.font.size = Pt(8.5)
        run.font.color.rgb = GRAY
    p.paragraph_format.space_after = Pt(4)


def build(result: dict, path: Path) -> None:
    doc = docx.Document()
    doc.styles["Normal"].font.size = Pt(11)
    kind = result["kind"]
    doc.add_heading(result.get("title") or kind.replace("_", " ").title(), level=0)
    names = ", ".join(d["name"] for d in result.get("documents", []))
    meta = doc.add_paragraph()
    run = meta.add_run(f"Workspace: {result.get('workspace', '')} · Sources: {names} · Prepared with GrepLens "
                       f"({'local AI' if result.get('provider') == 'local' else result.get('provider', '')}). Check each point "
                       "against its source before relying on it.")
    run.font.size = Pt(9)
    run.font.color.rgb = GRAY

    if kind == "briefing":
        if result.get("overview"):
            doc.add_heading("Overview", level=1)
            _para(doc, result["overview"])
        for sec in result.get("sections", []):
            doc.add_heading(sec["heading"] or "Key points", level=1)
            for p in sec["points"]:
                _point(doc, p["text"], p["sources"])
        if result.get("key_values"):
            doc.add_heading("Key values and dates", level=1)
            table = doc.add_table(rows=1, cols=3)
            table.style = "Table Grid"
            for cell, label in zip(table.rows[0].cells, ("Item", "Value", "Source")):
                cell.text = label
            for k in result["key_values"]:
                cells = table.add_row().cells
                cells[0].text, cells[1].text, cells[2].text = k["item"], k["value"], _sources(k["sources"])
        if result.get("open_questions"):
            doc.add_heading("Open questions and risks", level=1)
            for q in result["open_questions"]:
                _point(doc, q["text"], q["sources"])

    elif kind == "study_guide":
        if result.get("summary"):
            doc.add_heading("Summary", level=1)
            _para(doc, result["summary"])
        if result.get("concepts"):
            doc.add_heading("Key concepts", level=1)
            for c in result["concepts"]:
                _para(doc, c["term"], bold=True)
                _para(doc, c["explanation"])
                _cite(doc, c["sources"])
        if result.get("faq"):
            doc.add_heading("Frequently asked questions", level=1)
            for f in result["faq"]:
                _para(doc, f["question"], bold=True)
                _para(doc, f["answer"])
                _cite(doc, f["sources"])
        if result.get("glossary"):
            doc.add_heading("Glossary", level=1)
            table = doc.add_table(rows=1, cols=2)
            table.style = "Table Grid"
            table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Term", "Definition"
            for g in result["glossary"]:
                cells = table.add_row().cells
                cells[0].text, cells[1].text = g["term"], g["definition"]

    elif kind == "flashcards":
        if result.get("flashcards"):
            doc.add_heading("Flashcards", level=1)
            table = doc.add_table(rows=1, cols=2)
            table.style = "Table Grid"
            table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Front", "Back"
            for c in result["flashcards"]:
                cells = table.add_row().cells
                cells[0].text, cells[1].text = c["front"], c["back"]
        if result.get("quiz"):
            doc.add_heading("Quiz", level=1)
            for i, q in enumerate(result["quiz"], 1):
                _para(doc, f"{i}. {q['question']}", bold=True)
                for j, o in enumerate(q["options"]):
                    _para(doc, f"   {'ABCD'[j]}) {o}")
            doc.add_heading("Answers", level=2)
            for i, q in enumerate(result["quiz"], 1):
                _para(doc, f"{i}. {'ABCD'[q['answer']]} – {q['explanation']}")
                _cite(doc, q.get("sources", []))

    elif kind == "comparison":
        names = [d["name"] for d in result["documents"]]
        table = doc.add_table(rows=1, cols=len(names) + 1)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.rows[0].cells[0].text = "Criterion"
        for i, n in enumerate(names, 1):
            table.rows[0].cells[i].text = n
        for row in result["rows"]:
            cells = table.add_row().cells
            cells[0].text = row["criterion"]
            for i, cell in enumerate(row["cells"], 1):
                text = cell["text"] or "Not found"
                if cell["sources"]:
                    text += f"\n({cell['sources'][0]['location']})"
                cells[i].text = text

    elif kind == "timeline":
        table = doc.add_table(rows=1, cols=3)
        table.style = "Table Grid"
        for cell, label in zip(table.rows[0].cells, ("Date", "Event", "Source")):
            cell.text = label
        for e in result["events"]:
            cells = table.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = e["date"], e["event"], _sources(e["sources"])

    elif kind == "mindmap":
        for theme in result.get("children", []):
            doc.add_heading(theme["title"], level=1)
            for p in theme.get("children", []):
                _point(doc, p["title"], p.get("sources", []))

    elif kind == "literature_review":
        if result.get("introduction"):
            doc.add_heading("Introduction", level=1)
            _para(doc, result["introduction"])
        for t in result.get("themes", []):
            doc.add_heading(t["heading"], level=1)
            _para(doc, t["text"])
            _cite(doc, t["sources"])
        if result.get("gaps"):
            doc.add_heading("Gaps and open questions", level=1)
            _para(doc, result["gaps"])
        if result.get("conclusion"):
            doc.add_heading("Conclusion", level=1)
            _para(doc, result["conclusion"])
        doc.add_heading("Sources reviewed", level=1)
        for r in result.get("references", []):
            _para(doc, f"[{r['n']}] {r['document']}")

    elif kind == "slides":
        for i, s in enumerate(result.get("slides", []), 1):
            doc.add_heading(f"Slide {i}: {s['title']}", level=1)
            for b in s["bullets"]:
                _point(doc, b, [])
            if s.get("notes"):
                p = _para(doc, f"Speaker notes: {s['notes']}")
                p.runs[0].italic = True
            _cite(doc, s["sources"])
    doc.save(path)
