"""Document review workflow built with LangGraph.

  prepare → review_section ⇄ verify_section → cross_checks → finalize

- prepare: loads the document, splits it into sections (by article, max ~6,000 characters) and
  builds the instructions from the chosen categories, a fixed checklist, or a specific instruction.
- review_section: asks the review model for findings in one section, each with an exact quote.
- verify_section: keeps only findings whose quote is really in the section text, attaches the exact
  location, and saves them at once (so progress survives a pause or restart).
- cross_checks: rule-based checks across the whole document (placeholders left blank,
  cross-references to missing clauses, the same amount stated in several places).
- finalize: marks the review complete.
Pause/resume: the position is stored after each section; resuming restarts from that section.
"""
from __future__ import annotations

import json
import re
import threading
import traceback
from collections import defaultdict
from difflib import SequenceMatcher
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .. import db, engine, llm, profiles
from ..ingest import segments_of

SECTION_CHARS = 6000
_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ـ": ""})


class ReviewState(TypedDict, total=False):
    review_id: str
    document_id: str
    sections: list
    position: int
    instructions: str
    pending: list
    stop: bool


def normalize(text: str) -> str:
    text = re.sub(r"[ً-ْ]", "", text.translate(_LETTERS))
    return re.sub(r"\s+", " ", text.replace("“", '"').replace("”", '"').replace("’", "'")).strip().lower()


def build_sections(segments: list[dict]) -> list[dict]:
    groups: list[list[dict]] = []
    for seg in segments:
        key = seg["article"] or f"page-{seg['page']}"
        if groups and (groups[-1][0]["article"] or f"page-{groups[-1][0]['page']}") == key \
                and sum(len(s["text"]) for s in groups[-1]) + len(seg["text"]) <= SECTION_CHARS:
            groups[-1].append(seg)
        else:
            groups.append([seg])
    return [{"title": g[0]["article"] or f"Page {g[0]['page']}", "segment_ids": [s["id"] for s in g]} for g in groups]


def build_instructions(config: dict) -> str:
    chosen = profiles.get_many(config.get("categories") or [])
    personas = " and ".join(p["persona"] for p in chosen)
    mode = config.get("mode", "open")
    if mode == "checklist":
        items = [c.strip() for c in config.get("checklist", []) if c.strip()]
        body = "Check the section against each item of this CHECKLIST and report every item it fails:\n" + \
               "\n".join(f"- {c}" for c in items)
    elif mode == "instruction":
        body = f"Perform this TASK on the section and report each result as a finding:\n{config.get('instruction', '')}"
    else:
        focus = [f for p in chosen for f in p["focus"]]
        body = "Look for issues in these FOCUS AREAS:\n" + "\n".join(f"- {f}" for f in dict.fromkeys(focus))
    names = " + ".join(p["name"] for p in chosen)
    return (f"You review one section of a document as {personas}.\n{body}\n\n"
            "Rules: report only real issues in THIS section's text. For every finding copy the affected words "
            "exactly from the section into \"quote\" (at most 30 words). Write in the language of the section.\n"
            'Return only JSON: {"findings": [{"severity": "high|medium|low", "category": "' + names +
            '", "title": "short title", "quote": "exact words", "recommendation": "what to change"}]}. '
            'Return {"findings": []} when the section has no issue.')


def locate(quote: str, segments: list[dict]) -> dict | None:
    """Find the segment that really contains the quote (exact, else ≥85% fuzzy match)."""
    q = normalize(quote)
    if len(q) < 4:
        return None
    for seg in segments:
        if q in normalize(seg["text"]):
            return seg
    best, best_ratio = None, 0.0
    for seg in segments:
        text = normalize(seg["text"])
        matched = sum(b.size for b in SequenceMatcher(None, q, text, autojunk=False).get_matching_blocks())
        ratio = matched / len(q)
        if ratio > best_ratio:
            best, best_ratio = seg, ratio
    return best if best_ratio >= 0.85 else None


def _save_finding(state: ReviewState, item: dict, seg: dict | None, location: str | None = None) -> None:
    db.insert("findings", {
        "review_id": state["review_id"], "document_id": state["document_id"],
        "severity": str(item.get("severity", "medium")).lower() if str(item.get("severity", "")).lower()
        in ("high", "medium", "low") else "medium",
        "category": str(item.get("category") or "General")[:60], "title": str(item.get("title") or "Finding")[:200],
        "location": location or (seg["location"] if seg else ""), "quote": str(item.get("quote", ""))[:500],
        "recommendation": str(item.get("recommendation", ""))[:1000], "page": seg["page"] if seg else 0,
        "segment_id": seg["id"] if seg else None, "created": db.now()})


# ------------------------------------------------------------------ graph nodes
def prepare(state: ReviewState) -> ReviewState:
    review = db.get("reviews", state["review_id"])
    segments = segments_of(state["document_id"])
    sections = build_sections(segments)
    db.update("reviews", state["review_id"], {"status": "running", "total": len(sections), "updated": db.now()})
    return {"sections": sections, "position": review.get("position", 0) or 0,
            "instructions": build_instructions(review["config"]), "stop": False}


def review_section(state: ReviewState) -> ReviewState:
    section = state["sections"][state["position"]]
    segs = [db.get("segments", sid) for sid in section["segment_ids"]]
    text = "\n\n".join(f"({s['location']})\n{s['text']}" for s in segs)
    messages = [{"role": "system", "content": state["instructions"]},
                {"role": "user", "content": f"SECTION: {section['title']}\n\n{text}"}]

    def fake() -> str:
        first = segs[0]["text"].split(".")[0][:120]
        return json.dumps({"findings": [{"severity": "medium", "category": "General", "title": "Sample finding",
                                         "quote": first, "recommendation": "Review this wording."},
                                        {"severity": "high", "title": "Invented", "quote": "words not in the text at all",
                                         "recommendation": "-"}]})

    raw = llm.chat("review", messages, json_mode=True, max_tokens=1200, fake=fake)
    data = llm.parse_json(raw, {"findings": []})
    items = data.get("findings", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    return {"pending": [i for i in items if isinstance(i, dict)]}


def verify_section(state: ReviewState) -> ReviewState:
    section = state["sections"][state["position"]]
    segs = [db.get("segments", sid) for sid in section["segment_ids"]]
    for item in state.get("pending", []):
        seg = locate(str(item.get("quote", "")), segs)
        if seg:  # drop any finding the model cannot tie to the real text
            _save_finding(state, item, seg)
    position = state["position"] + 1
    current = db.get("reviews", state["review_id"])
    db.update("reviews", state["review_id"], {"position": position, "updated": db.now(),
                                              "message": f"Reviewed section {position} of {len(state['sections'])}"})
    return {"position": position, "pending": [], "stop": current["status"] in ("pausing", "cancelled")}


def route(state: ReviewState) -> str:
    if state.get("stop"):
        return END
    return "review_section" if state["position"] < len(state["sections"]) else "cross_checks"


PLACEHOLDER_RE = re.compile(r"\[(?:[A-Z][A-Z ]{1,40}|X{2,}|\d{1,6}(?:\.\d+)?|TBD|TBC|\.{2,}|…)\]|\bTB[DC]\b|_{4,}")
XREF_RE = re.compile(r"\b(Clause|Sub-Clause|Article)\s+(\d+(?:\.\d+)*)")
AMOUNT_RE = re.compile(r"\b(?:SAR|SR|USD|ريال)\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\b\d{1,3}(?:,\d{3}){1,}(?:\.\d+)?\s?(?:SAR|SR|ريال)")


def cross_checks(state: ReviewState) -> ReviewState:
    segments = segments_of(state["document_id"])
    clauses = {s["clause"] for s in segments if s["clause"]}
    articles = {s["article"].split()[-1] for s in segments if s["article"]}
    amounts: dict[str, list[dict]] = defaultdict(list)
    for seg in segments:
        for m in PLACEHOLDER_RE.finditer(seg["text"]):
            _save_finding(state, {"severity": "medium", "category": "General", "title": "Value or text left blank",
                                  "quote": seg["text"][max(0, m.start() - 40):m.end() + 40].strip(),
                                  "recommendation": f"Complete the placeholder {m.group(0)} before issuing."}, seg)
        for m in XREF_RE.finditer(seg["text"]):
            kind, number = m.group(1), m.group(2)
            exists = number in articles if kind == "Article" else (number in clauses or number.split(".")[0] in articles)
            if clauses and not exists:
                _save_finding(state, {"severity": "medium", "category": "General",
                                      "title": f"{kind} {number} is referenced but not found",
                                      "quote": m.group(0), "recommendation": "Check the cross-reference number."}, seg)
        for m in AMOUNT_RE.finditer(seg["text"]):
            amounts[re.sub(r"\D", "", m.group(0))].append(seg)
    for _value, segs in amounts.items():
        places = {s["clause"] or s["location"] for s in segs}
        if len(places) > 1:
            _save_finding(state, {"severity": "low", "category": "General", "title": "Same amount stated more than once",
                                  "quote": AMOUNT_RE.search(segs[1]["text"]).group(0),
                                  "recommendation": "State the value once and cross-reference it elsewhere."}, segs[1])
    return {}


def finalize(state: ReviewState) -> ReviewState:
    db.update("reviews", state["review_id"], {"status": "done", "message": "Review complete", "updated": db.now()})
    engine.stop("review")  # free the memory used by the larger review model
    return {}


def _graph():
    graph = StateGraph(ReviewState)
    for name, fn in [("prepare", prepare), ("review_section", review_section), ("verify_section", verify_section),
                     ("cross_checks", cross_checks), ("finalize", finalize)]:
        graph.add_node(name, fn)
    graph.add_edge(START, "prepare")
    graph.add_conditional_edges("prepare", route, ["review_section", "cross_checks", END])
    graph.add_edge("review_section", "verify_section")
    graph.add_conditional_edges("verify_section", route, ["review_section", "cross_checks", END])
    graph.add_edge("cross_checks", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


GRAPH = _graph()


# ------------------------------------------------------------------ public API
def start(document_id: str, config: dict) -> dict:
    review = db.insert("reviews", {"document_id": document_id, "config": config, "status": "queued", "position": 0,
                                   "total": 0, "created": db.now(), "updated": db.now()})
    _launch(review["id"], document_id)
    return review


def _launch(review_id: str, document_id: str) -> None:
    def run():
        try:
            GRAPH.invoke({"review_id": review_id, "document_id": document_id}, {"recursion_limit": 100_000})
            if db.get("reviews", review_id)["status"] == "pausing":
                db.update("reviews", review_id, {"status": "paused", "message": "Paused"})
        except Exception as exc:
            traceback.print_exc()
            db.update("reviews", review_id, {"status": "error", "message": str(exc)})
    threading.Thread(target=run, daemon=True, name=f"review-{review_id[:6]}").start()


def pause(review_id: str) -> None:
    db.update("reviews", review_id, {"status": "pausing"})


def resume(review_id: str) -> None:
    review = db.get("reviews", review_id)
    if review and review["status"] in ("paused", "pausing", "error"):
        db.update("reviews", review_id, {"status": "running"})
        _launch(review_id, review["document_id"])


def resume_interrupted() -> None:
    """Continue reviews that were running when the app closed."""
    for review in db.query("SELECT * FROM reviews WHERE status IN ('running', 'queued')"):
        _launch(review["id"], review["document_id"])


def get(review_id: str) -> dict | None:
    review = db.get("reviews", review_id)
    if review:
        review["findings"] = db.query("SELECT * FROM findings WHERE review_id = ? ORDER BY "
                                      "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, page",
                                      (review_id,))
    return review


def latest(document_id: str) -> dict | None:
    rows = db.query("SELECT id FROM reviews WHERE document_id = ? ORDER BY created DESC LIMIT 1", (document_id,))
    return get(rows[0]["id"]) if rows else None
