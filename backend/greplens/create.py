"""Create panel: outputs built from several documents of a workspace, each point linked to its source.

  briefing           summary and briefing document
  study_guide        study guide, FAQ and glossary
  flashcards         flashcards and quiz
  comparison         comparison table across documents, by criteria
  timeline           dated events across documents
  mindmap            mind map of themes
  literature_review  literature review draft with references
  slides             slide outline (exported to Word)

Most outputs work in two steps: read every part of each document and note the relevant points with an exact
quote (verified against the text, so each point keeps its real location), then write the output from those
points. Comparison and timeline read the documents directly. All outputs export to Word.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import db, index, instructions, llm, providers
from .ingest import segments_of
from .jobs import Job, task
from .review import locate
from .tools import groups, language_rule, text_of

KINDS = ["briefing", "study_guide", "flashcards", "comparison", "timeline", "mindmap", "literature_review", "slides"]
PURPOSE = {
    "briefing": "the main facts, decisions, obligations, values, dates, risks and open questions",
    "study_guide": "key concepts, definitions, methods, results and anything a student should be able to explain",
    "flashcards": "facts, definitions and relationships worth memorizing",
    "mindmap": "the main topics and their key points",
    "literature_review": "the aim, method, main findings, limitations and conclusions",
    "slides": "the most important messages, figures and conclusions for a presentation",
}
QUICK_GROUPS = 8          # parts read per document in Quick mode


def _local() -> bool:
    return providers.current() == providers.LOCAL


def _limits() -> tuple[int, int]:
    """(characters per part read, characters of points given to the final step)."""
    return (6000, 9000) if _local() else (24000, 90000)


def _docs(job: Job) -> list[dict]:
    rows = db.query("SELECT * FROM documents WHERE workspace_id = ? AND status = 'ready' ORDER BY created",
                    (job.workspace_id,))
    wanted = job.params.get("document_ids") or []
    rows = [d for d in rows if not wanted or d["id"] in wanted]
    if not rows:
        raise ValueError("Choose at least one document that is ready.")
    return rows


def _name(doc: dict) -> str:
    return re.sub(r"\.[^.]+$", "", doc["filename"])


def _system(job: Job, text: str, task_text: str) -> str:
    extra = instructions.for_prompt(job.workspace_id, task_text)
    return text + (f"\n\n{extra}" if extra else "")


def _cite(doc: dict, seg: dict | None, fallback: list[dict]) -> dict:
    seg = seg or fallback[0]
    return {"document_id": doc["id"], "document": _name(doc), "location": seg["location"], "page": seg["page"],
            "segment_id": seg["id"]}


# ------------------------------------------------------------------ step 1: notes from every part
def _digest(job: Job, docs: list[dict], purpose: str, lang: str, start: float, end: float) -> list[dict]:
    limit, _ = _limits()
    plan = []
    for doc in docs:
        parts = groups(segments_of(doc["id"]), limit)
        if job.params.get("depth") == "quick" and len(parts) > QUICK_GROUPS:
            step = len(parts) / QUICK_GROUPS
            parts = [parts[int(i * step)] for i in range(QUICK_GROUPS)]
        plan += [(doc, part) for part in parts]
    points: list[dict] = []
    for i, (doc, part) in enumerate(plan, 1):
        job.progress(start + (end - start) * (i - 1) / max(1, len(plan)), f"Reading {_name(doc)} · part {i} of {len(plan)}")
        system = _system(job, f"From this part of the document “{_name(doc)}”, note up to 6 points about {purpose}. "
                              "Each point is one or two sentences. For each, copy an exact quote of at most 25 words from "
                              f"the text. {lang} Return only JSON: " + '{"points": [{"point": "...", "quote": "..."}]}. '
                              'Return {"points": []} if nothing in this part is relevant.', purpose)

        def fake(part=part) -> str:
            return json.dumps({"points": [{"point": part[0]["text"][:160], "quote": part[0]["text"][:80]}]})

        raw = llm.chat("fast", [{"role": "system", "content": system}, {"role": "user", "content": text_of(part)}],
                       json_mode=True, max_tokens=900, fake=fake)
        data = llm.parse_json(raw, {})
        for item in (data.get("points") or []) if isinstance(data, dict) else []:
            if not isinstance(item, dict) or not str(item.get("point", "")).strip():
                continue
            seg = locate(str(item.get("quote", "")), part)
            points.append({"id": len(points) + 1, "text": str(item["point"]).strip()[:600],
                           "cite": _cite(doc, seg, part), "verified": bool(seg)})
    if not points:
        raise ValueError("No relevant content was found in the chosen documents.")
    return points


def _numbered(points: list[dict]) -> str:
    return "\n".join(f"[{p['id']}] ({p['cite']['document']}) {p['text']}" for p in points)


def _condense(job: Job, points: list[dict], lang: str) -> list[dict]:
    """Merge points until they fit the final step. Merged points keep the numbers of their sources."""
    _, budget = _limits()
    current = [{"id": p["id"], "text": p["text"], "sources": [p["id"]]} for p in points]
    rounds = 0
    while sum(len(p["text"]) + 12 for p in current) > budget and rounds < 4:
        rounds += 1
        batches, batch, size = [], [], 0
        for p in current:
            if batch and size + len(p["text"]) > budget:
                batches.append(batch)
                batch, size = [], 0
            batch.append(p)
            size += len(p["text"]) + 12
        if batch:
            batches.append(batch)
        merged = []
        for i, batch in enumerate(batches, 1):
            job.progress(0.75, f"Combining notes · {i} of {len(batches)}")
            by_id = {p["id"]: p for p in batch}
            raw = llm.chat("fast", [
                {"role": "system", "content": "Merge these numbered notes: combine repeats, keep every distinct fact, value "
                                              f"and date. {lang} Return only JSON: " +
                                              '{"points": [{"point": "...", "sources": [note numbers]}]}'},
                {"role": "user", "content": "\n".join(f"[{p['id']}] {p['text']}" for p in batch)}],
                json_mode=True, max_tokens=2000,
                fake=lambda batch=batch: json.dumps({"points": [{"point": p["text"], "sources": [p["id"]]} for p in batch[: max(1, len(batch) // 2)]]}))
            data = llm.parse_json(raw, {})
            got = [x for x in (data.get("points") or []) if isinstance(x, dict)] if isinstance(data, dict) else []
            if not got:  # keep the batch as it was rather than lose content
                merged += batch
                continue
            for x in got:
                src = sorted({s for n in _ints(x.get("sources")) if n in by_id for s in by_id[n]["sources"]})
                merged.append({"text": str(x.get("point", "")).strip()[:600], "sources": src or batch[0]["sources"]})
        current = [{**p, "id": i} for i, p in enumerate(merged, 1)]
        if len(current) >= sum(len(b) for b in batches):
            break  # nothing merged; stop rather than loop
    return current


def _ints(values) -> list[int]:
    out = []
    for v in values or []:
        try:
            out.append(int(str(v).strip("[] S")))
        except ValueError:
            continue
    return out


class _Sources:
    """Turn the note numbers used by the final step into citations (document, location, segment)."""

    def __init__(self, points: list[dict], condensed: list[dict]):
        self.original = {p["id"]: p["cite"] for p in points}
        self.condensed = {c["id"]: c["sources"] for c in condensed}

    def __call__(self, numbers) -> list[dict]:
        cites, seen = [], set()
        for n in _ints(numbers):
            for orig in self.condensed.get(n, []):
                cite = self.original.get(orig)
                key = cite and (cite["document_id"], cite["segment_id"])
                if cite and key not in seen:
                    seen.add(key)
                    cites.append(cite)
        return cites[:4]


def _final(job: Job, kind: str, condensed: list[dict], lang: str, instructions_text: str, schema: str,
           max_tokens: int = 2600) -> dict:
    job.progress(0.85, "Writing")
    numbered = "\n".join(f"[{c['id']}] {c['text']}" for c in condensed)
    system = _system(job, f"{instructions_text}\nUse only the NOTES. Cite note numbers in \"sources\". {lang}\n"
                          f"Return only JSON in this form:\n{schema}", instructions_text)
    raw = llm.chat("review", [{"role": "system", "content": system}, {"role": "user", "content": f"NOTES:\n{numbered}"}],
                   json_mode=True, max_tokens=max_tokens, fake=lambda: _fake_final(kind, condensed))
    data = llm.parse_json(raw, None)
    return data if isinstance(data, dict) else {}


def _fake_final(kind: str, c: list[dict]) -> str:
    first = c[0]
    item = {"text": first["text"][:100], "sources": [first["id"]]}
    return json.dumps({
        "briefing": {"title": "Briefing", "overview": first["text"][:120], "sections": [{"heading": "Key points", "points": [item]}],
                     "key_values": [{"item": "Value", "value": "10%", "sources": [first["id"]]}], "open_questions": [item]},
        "study_guide": {"summary": first["text"][:100], "concepts": [{"term": "Term", "explanation": "Meaning", "sources": [1]}],
                        "faq": [{"question": "What?", "answer": "This.", "sources": [1]}],
                        "glossary": [{"term": "Term", "definition": "Meaning", "sources": [1]}]},
        "flashcards": {"flashcards": [{"front": "Front", "back": "Back", "sources": [1]}],
                       "quiz": [{"question": "Q?", "options": ["A", "B", "C", "D"], "answer": 0, "explanation": "A.", "sources": [1]}]},
        "mindmap": {"title": "Map", "themes": [{"title": "Theme", "points": [item]}]},
        "literature_review": {"title": "Review", "introduction": "Intro.", "themes": [{"heading": "Theme", "text": "Text.", "sources": [1]}],
                              "gaps": "Gaps.", "conclusion": "End."},
        "slides": {"title": "Deck", "slides": [{"title": "Slide", "bullets": ["Point"], "notes": "Say this.", "sources": [1]}]},
    }[kind])


def _prepare(job: Job, kind: str) -> tuple[list[dict], list[dict], list[dict], str]:
    docs = _docs(job)
    sample = " ".join(s["text"] for s in segments_of(docs[0]["id"])[:20])
    lang = language_rule(job.params.get("language", "auto"), sample)
    focus = str(job.params.get("focus") or "").strip()
    purpose = PURPOSE[kind] + (f", especially: {focus}" if focus else "")
    points = _digest(job, docs, purpose, lang, 0.02, 0.7)
    condensed = _condense(job, points, lang)
    return docs, points, condensed, lang


def _finish(job: Job, kind: str, result: dict, docs: list[dict]) -> dict:
    ws = db.get("workspaces", job.workspace_id)
    result.update(kind=kind, documents=[{"id": d["id"], "name": _name(d)} for d in docs],
                  provider=providers.current(), workspace=ws["name"] if ws else "")
    from .create_docx import build
    path = job.output(f"{kind}.docx")
    build(result, path)
    result["_file"] = path
    return result


# ------------------------------------------------------------------ 1. summary and briefing
@task("create_briefing")
def briefing(job: Job) -> dict:
    docs, points, condensed, lang = _prepare(job, "briefing")
    src = _Sources(points, condensed)
    data = _final(job, "briefing", condensed, lang,
                  "Write a briefing document across all the sources: an overview, sections of key points, the key "
                  "values and dates, and open questions or risks.",
                  '{"title": "...", "overview": "3-5 sentences", "sections": [{"heading": "...", "points": [{"text": "...", '
                  '"sources": [1, 2]}]}], "key_values": [{"item": "...", "value": "...", "sources": [3]}], '
                  '"open_questions": [{"text": "...", "sources": [4]}]}')
    if not data.get("sections"):
        data = {"title": "Briefing", "overview": "", "sections": [{"heading": "Key points",
                "points": [{"text": c["text"], "sources": [c["id"]]} for c in condensed[:40]]}]}
    out = {"title": str(data.get("title") or "Briefing"), "overview": str(data.get("overview") or ""),
           "sections": [{"heading": str(s.get("heading", "")), "points": [{"text": str(p.get("text", "")), "sources": src(p.get("sources"))}
                                                                          for p in s.get("points") or [] if isinstance(p, dict)]}
                        for s in data.get("sections") or [] if isinstance(s, dict)],
           "key_values": [{"item": str(k.get("item", "")), "value": str(k.get("value", "")), "sources": src(k.get("sources"))}
                          for k in data.get("key_values") or [] if isinstance(k, dict)],
           "open_questions": [{"text": str(q.get("text", "")), "sources": src(q.get("sources"))}
                              for q in data.get("open_questions") or [] if isinstance(q, dict)]}
    return _finish(job, "briefing", out, docs)


# ------------------------------------------------------------------ 2. study guide, FAQ and glossary
@task("create_study_guide")
def study_guide(job: Job) -> dict:
    docs, points, condensed, lang = _prepare(job, "study_guide")
    src = _Sources(points, condensed)
    data = _final(job, "study_guide", condensed, lang,
                  "Write a study guide: a short summary, the key concepts explained simply, frequently asked questions "
                  "with answers, and a glossary of terms.",
                  '{"summary": "...", "concepts": [{"term": "...", "explanation": "...", "sources": [1]}], '
                  '"faq": [{"question": "...", "answer": "...", "sources": [2]}], '
                  '"glossary": [{"term": "...", "definition": "...", "sources": [3]}]}')
    out = {"title": "Study guide", "summary": str(data.get("summary") or ""),
           "concepts": [{"term": str(c.get("term", "")), "explanation": str(c.get("explanation", "")), "sources": src(c.get("sources"))}
                        for c in data.get("concepts") or [] if isinstance(c, dict)],
           "faq": [{"question": str(f.get("question", "")), "answer": str(f.get("answer", "")), "sources": src(f.get("sources"))}
                   for f in data.get("faq") or [] if isinstance(f, dict)],
           "glossary": [{"term": str(g.get("term", "")), "definition": str(g.get("definition", "")), "sources": src(g.get("sources"))}
                        for g in data.get("glossary") or [] if isinstance(g, dict)]}
    if not (out["concepts"] or out["faq"] or out["glossary"]):
        raise ValueError("The AI did not return a usable study guide. Try again, or choose fewer documents.")
    return _finish(job, "study_guide", out, docs)


# ------------------------------------------------------------------ 3. flashcards and quiz
@task("create_flashcards")
def flashcards(job: Job) -> dict:
    count = max(4, min(30, int(job.params.get("count") or 12)))
    docs, points, condensed, lang = _prepare(job, "flashcards")
    src = _Sources(points, condensed)
    data = _final(job, "flashcards", condensed, lang,
                  f"Write {count} flashcards (a short prompt on the front, the answer on the back) and {count // 2} "
                  "multiple-choice quiz questions with exactly 4 options and one correct answer.",
                  '{"flashcards": [{"front": "...", "back": "...", "sources": [1]}], "quiz": [{"question": "...", '
                  '"options": ["...", "...", "...", "..."], "answer": 0, "explanation": "...", "sources": [2]}]}')
    quiz = []
    for q in data.get("quiz") or []:
        if isinstance(q, dict) and isinstance(q.get("options"), list) and len(q["options"]) >= 2:
            options = [str(o) for o in q["options"][:4]]
            answer = _ints([q.get("answer")])
            cites = src(q.get("sources"))
            quiz.append({"question": str(q.get("question", "")), "options": options,
                         "answer": answer[0] if answer and 0 <= answer[0] < len(options) else 0,
                         "explanation": str(q.get("explanation", "")), "sources": cites,
                         "location": cites[0]["location"] if cites else "", "segment_id": cites[0]["segment_id"] if cites else "",
                         "page": cites[0]["page"] if cites else 0, "document_id": cites[0]["document_id"] if cites else ""})
    out = {"title": "Flashcards and quiz",
           "flashcards": [{"front": str(c.get("front", "")), "back": str(c.get("back", "")), "sources": src(c.get("sources"))}
                          for c in data.get("flashcards") or [] if isinstance(c, dict)],
           "quiz": quiz}
    if not out["flashcards"] and not quiz:
        raise ValueError("The AI did not return usable flashcards. Try again, or choose fewer documents.")
    return _finish(job, "flashcards", out, docs)


# ------------------------------------------------------------------ 4. comparison table
@task("create_comparison")
def comparison(job: Job) -> dict:
    docs = _docs(job)
    if len(docs) < 2:
        raise ValueError("Choose at least two documents to compare.")
    sample = " ".join(s["text"] for s in segments_of(docs[0]["id"])[:20])
    lang = language_rule(job.params.get("language", "auto"), sample)
    criteria = [c.strip() for c in job.params.get("criteria") or [] if str(c).strip()][:15]
    if not criteria:
        job.progress(0.02, "Choosing criteria")
        heads = "\n\n".join(f"{_name(d)}:\n" + " ".join(s["text"] for s in segments_of(d["id"])[:30])[:1500] for d in docs)
        raw = llm.chat("fast", [{"role": "system", "content": "Propose 6 criteria to compare these documents side by side "
                                                              f"(short labels). {lang} Return only JSON: " + '{"criteria": ["...", "..."]}'},
                                {"role": "user", "content": heads}], json_mode=True, max_tokens=300,
                       fake=lambda: json.dumps({"criteria": ["Scope", "Value"]}))
        data = llm.parse_json(raw, {})
        criteria = [str(c)[:80] for c in (data.get("criteria") or [])][:8] if isinstance(data, dict) else []
        if not criteria:
            raise ValueError("Enter the criteria to compare.")
    rows, total, done = [], len(criteria) * len(docs), 0
    for criterion in criteria:
        cells = []
        for doc in docs:
            done += 1
            job.progress(0.05 + 0.9 * (done - 1) / total, f"{criterion} · {_name(doc)}")
            hits = index.retrieve(job.workspace_id, doc["id"], criterion, k=4)
            segs = [s for s in segments_of(doc["id"]) if s["id"] in {x for h in hits for x in h["segment_ids"]}]
            passages = "\n\n".join(f"({h['location']})\n{h['text']}" for h in hits)
            system = _system(job, f"State what this document says about the criterion, in at most 40 words, and copy an exact "
                                  f"quote of at most 25 words. {lang} If the passages do not cover it, set found to false. "
                                  'Return only JSON: {"found": true, "answer": "...", "quote": "..."}', criterion)
            raw = llm.chat("fast", [{"role": "system", "content": system},
                                    {"role": "user", "content": f"CRITERION: {criterion}\nDOCUMENT: {_name(doc)}\n\nPASSAGES:\n{passages or '(none)'}"}],
                           json_mode=True, max_tokens=300,
                           fake=lambda hits=hits: json.dumps({"found": bool(hits), "answer": hits[0]["text"][:80] if hits else "",
                                                              "quote": hits[0]["text"][:60] if hits else ""}))
            data = llm.parse_json(raw, {})
            data = data if isinstance(data, dict) else {}
            seg = locate(str(data.get("quote", "")), segs) if segs else None
            if seg is None and hits:  # quote not matched: point to the best search hit, not the start of the document
                top = hits[0]["segment_ids"][0] if hits[0]["segment_ids"] else None
                seg = next((s for s in segs if s["id"] == top), None)
            found = bool(data.get("found")) and bool(str(data.get("answer", "")).strip())
            cells.append({"document_id": doc["id"], "text": str(data.get("answer", "")).strip() if found else "",
                          "found": found, "sources": [_cite(doc, seg, segs)] if found and segs else []})
        rows.append({"criterion": criterion, "cells": cells})
    return _finish(job, "comparison", {"title": "Comparison", "criteria": criteria, "rows": rows}, docs)


# ------------------------------------------------------------------ 5. timeline
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})


def date_key(text: str) -> tuple:
    t = text.lower().replace(",", " ")
    if m := re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t):
        return (int(m[1]), int(m[2]), int(m[3]))
    if m := re.search(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b", t):
        return (int(m[3]), int(m[2]), int(m[1]))
    if m := re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\s+(\d{4})\b", t):
        if m[2] in MONTHS:
            return (int(m[3]), MONTHS[m[2]], int(m[1]))
    if m := re.search(r"\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\d{4})\b", t):
        if m[1] in MONTHS:
            return (int(m[3]), MONTHS[m[1]], int(m[2]))
    if m := re.search(r"\b([a-z]+)\s+(\d{4})\b", t):
        if m[1] in MONTHS:
            return (int(m[2]), MONTHS[m[1]], 0)
    if m := re.search(r"\b(1[89]\d\d|20\d\d)\b", t):
        return (int(m[1]), 0, 0)
    return (9999, 0, 0)


@task("create_timeline")
def timeline(job: Job) -> dict:
    docs = _docs(job)
    sample = " ".join(s["text"] for s in segments_of(docs[0]["id"])[:20])
    lang = language_rule(job.params.get("language", "auto"), sample)
    limit, _ = _limits()
    plan = [(doc, part) for doc in docs for part in groups(segments_of(doc["id"]), limit)]
    events = []
    for i, (doc, part) in enumerate(plan, 1):
        job.progress(0.02 + 0.9 * (i - 1) / len(plan), f"Reading {_name(doc)} · part {i} of {len(plan)}")
        system = _system(job, "List the dated events, deadlines and periods in this text. Use the date exactly as written. "
                              f"{lang} Return only JSON: " + '{"events": [{"date": "...", "event": "short description", '
                              '"quote": "exact words with the date, at most 25"}]}. Return {"events": []} if none.', "dates deadlines")
        raw = llm.chat("fast", [{"role": "system", "content": system}, {"role": "user", "content": text_of(part)}],
                       json_mode=True, max_tokens=900,
                       fake=lambda part=part: json.dumps({"events": [{"date": m.group(0), "event": "Date mentioned", "quote": m.group(0)}
                                                                     for s in part for m in [re.search(r"\b\d{1,2}\s\w+\s\d{4}\b|\b\d+\s(?:days|months)\b", s["text"])] if m][:3]}))
        data = llm.parse_json(raw, {})
        for e in (data.get("events") or []) if isinstance(data, dict) else []:
            if not isinstance(e, dict) or not str(e.get("date", "")).strip():
                continue
            seg = locate(str(e.get("quote", "")), part)
            if not seg:
                continue  # keep only events tied to the real text
            events.append({"date": str(e["date"]).strip()[:60], "event": str(e.get("event", "")).strip()[:300],
                           "sources": [_cite(doc, seg, part)], "_key": date_key(str(e["date"]))})
    events.sort(key=lambda e: e["_key"])
    for e in events:
        e["dated"] = e.pop("_key")[0] != 9999
    return _finish(job, "timeline", {"title": "Timeline", "events": events}, docs)


# ------------------------------------------------------------------ 6. mind map
@task("create_mindmap")
def mind_map(job: Job) -> dict:
    docs, points, condensed, lang = _prepare(job, "mindmap")
    src = _Sources(points, condensed)
    data = _final(job, "mindmap", condensed, lang,
                  "Group the notes into 3 to 7 themes for a mind map; each theme has 2 to 6 short points (under 12 words).",
                  '{"title": "...", "themes": [{"title": "...", "points": [{"text": "...", "sources": [1]}]}]}', max_tokens=1600)
    ws = db.get("workspaces", job.workspace_id)
    children = []
    for theme in data.get("themes") or []:
        if not isinstance(theme, dict):
            continue
        kids = []
        for p in theme.get("points") or []:
            if isinstance(p, dict):
                cites = src(p.get("sources"))
                kids.append({"title": str(p.get("text", ""))[:120], "sources": cites,
                             **({"segment_id": cites[0]["segment_id"], "page": cites[0]["page"], "document_id": cites[0]["document_id"]} if cites else {})})
        children.append({"title": str(theme.get("title", ""))[:80], "children": kids})
    if not children:
        raise ValueError("The AI did not return a usable mind map. Try again, or choose fewer documents.")
    return _finish(job, "mindmap", {"title": str(data.get("title") or (ws or {}).get("name") or "Mind map"), "children": children}, docs)


# ------------------------------------------------------------------ 7. literature review draft
@task("create_literature_review")
def literature_review(job: Job) -> dict:
    docs, points, condensed, lang = _prepare(job, "literature_review")
    src = _Sources(points, condensed)
    data = _final(job, "literature_review", condensed, lang,
                  "Write a literature review draft that synthesizes the sources by theme (not one source after another): "
                  "an introduction, themed sections comparing what the sources found, gaps and open questions, and a "
                  "conclusion. Academic tone. Do not invent sources or findings.",
                  '{"title": "...", "introduction": "...", "themes": [{"heading": "...", "text": "one or two paragraphs", '
                  '"sources": [1, 4]}], "gaps": "...", "conclusion": "..."}', max_tokens=3200)
    themes = [{"heading": str(t.get("heading", "")), "text": str(t.get("text", "")), "sources": src(t.get("sources"))}
              for t in data.get("themes") or [] if isinstance(t, dict)]
    if not themes:
        raise ValueError("The AI did not return a usable review. Try again, or choose fewer documents.")
    out = {"title": str(data.get("title") or "Literature review"), "introduction": str(data.get("introduction") or ""),
           "themes": themes, "gaps": str(data.get("gaps") or ""), "conclusion": str(data.get("conclusion") or ""),
           "references": [{"n": i, "document": _name(d), "document_id": d["id"]} for i, d in enumerate(docs, 1)]}
    return _finish(job, "literature_review", out, docs)


# ------------------------------------------------------------------ 8. slide outline
@task("create_slides")
def slides(job: Job) -> dict:
    count = max(4, min(25, int(job.params.get("count") or 10)))
    docs, points, condensed, lang = _prepare(job, "slides")
    src = _Sources(points, condensed)
    data = _final(job, "slides", condensed, lang,
                  f"Write an outline for a presentation of about {count} slides: a title slide, the main messages, and "
                  "a closing slide. Each slide has a title, 3 to 5 short bullets and speaker notes.",
                  '{"title": "...", "slides": [{"title": "...", "bullets": ["..."], "notes": "...", "sources": [1]}]}')
    out = {"title": str(data.get("title") or "Presentation outline"),
           "slides": [{"title": str(s.get("title", "")), "bullets": [str(b) for b in s.get("bullets") or []][:6],
                       "notes": str(s.get("notes", "")), "sources": src(s.get("sources"))}
                      for s in data.get("slides") or [] if isinstance(s, dict)]}
    if not out["slides"]:
        raise ValueError("The AI did not return a usable outline. Try again, or choose fewer documents.")
    return _finish(job, "slides", out, docs)


def output_file(job: dict) -> Path | None:
    return Path(job["file"]) if job and job.get("file") else None
