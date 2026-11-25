import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, Doc, download, InkStroke, normalize, Note, Segment, Workspace } from "../api";
import { useI18n } from "../i18n";
import { IconChevron, IconClose, IconDoc, IconDownload, IconGear, IconHighlight, IconPlus, IconSearch, IconShare, IconUnderline, Logo } from "../icons";
import type { View } from "../App";
import PdfView, { colorOf } from "./PdfView";
import TextView from "./TextView";
import NotesPanel from "./NotesPanel";
import AssistantPanel from "./AssistantPanel";
import AiPicker from "./AiPicker";
import InstructionsDialog from "./InstructionsDialog";
import { MindMapView, MindNode, Question, QuizView } from "./Tools";

export type Selection = { page: number; rects: number[][]; quote: string; segment_id: string | null; start: number | null;
  end: number | null; x: number; y: number };
export type Jump = { page?: number; box?: number[]; segment_id?: string; ts: number };
type OutlineItem = { level: number; label: string; title: string; page: number; segment_ids: string[]; approx?: boolean };
type Translation = { text: string; translation: string; glossary: boolean; note: string; source: string; target: string };

const COLORS = ["yellow", "green", "blue", "pink", "orange"];

export default function WorkspaceView({ id, go }: { id: string; go: (v: View) => void }) {
  const { t, lang, setLang } = useI18n();
  const [ws, setWs] = useState<Workspace | null>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [docId, setDocId] = useState<string | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [sel, setSel] = useState<Selection | null>(null);
  const [tool, setTool] = useState<"highlight" | "underline">("highlight");
  const [color, setColor] = useState("yellow");
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [tr, setTr] = useState<{ x: number; y: number; data: Translation | null; error?: string } | null>(null);
  const [jump, setJump] = useState<Jump | null>(null);
  const [pageInfo, setPageInfo] = useState<[number, number]>([1, 0]);
  const [askPrefill, setAskPrefill] = useState<{ text: string; ts: number } | null>(null);
  const [search, setSearch] = useState("");
  const [hits, setHits] = useState<{ q: string; list: Segment[]; i: number }>({ q: "", list: [], i: 0 });
  const [docMenu, setDocMenu] = useState(false);
  const [penOn, setPenOn] = useState(false);
  const [penColor, setPenColor] = useState("#D92D20");
  const [pending, setPending] = useState<Record<number, InkStroke[]>>({});
  const [outline, setOutline] = useState<OutlineItem[] | null>(null);
  const [outlineOpen, setOutlineOpen] = useState(false);
  const [sectionRequest, setSectionRequest] = useState<{ segment_ids: string[]; title: string; ts: number } | null>(null);
  const [mindMap, setMindMap] = useState<MindNode | null>(null);
  const [quiz, setQuiz] = useState<Question[] | null>(null);
  const [showInstructions, setShowInstructions] = useState(false);
  const handleSelect = useCallback((s: Selection | null) => { setSel(s); setMenu(null); }, []);
  const fileRef = useRef<HTMLInputElement>(null);
  const revRef = useRef<HTMLInputElement>(null);
  const doc = docs.find((d) => d.id === docId) ?? null;

  const loadWs = useCallback(() => api.get<Workspace & { documents: Doc[] }>(`/api/workspaces/${id}`).then((w) => {
    setWs(w); setDocs(w.documents);
    setDocId((cur) => cur && w.documents.some((d) => d.id === cur) ? cur : w.documents[w.documents.length - 1]?.id ?? null);
  }), [id]);
  useEffect(() => { loadWs(); }, [loadWs]);

  // follow import progress until the document is ready
  useEffect(() => {
    if (!docs.some((d) => d.status !== "ready" && d.status !== "error")) return;
    const timer = setInterval(loadWs, 1500);
    return () => clearInterval(timer);
  }, [docs, loadWs]);

  const loadNotes = useCallback(async () => {
    if (docId) setNotes(await api.get<Note[]>(`/api/documents/${docId}/notes`));
  }, [docId]);
  useEffect(() => {
    setSel(null); setActive(null); setSegments([]); setNotes([]); setOutline(null); setOutlineOpen(false);
    if (doc?.status === "ready") { api.get<Segment[]>(`/api/documents/${doc.id}/segments`).then(setSegments); loadNotes(); }
  }, [docId, doc?.status, loadNotes]); // eslint-disable-line react-hooks/exhaustive-deps

  // redaction marks are drawn on the page but are not notes
  const noteList = useMemo(() => notes.filter((n) => n.mark !== "redact"), [notes]);
  const redactCount = notes.length - noteList.length;
  const numbers = useMemo(() => Object.fromEntries(noteList.map((n, i) => [n.id, i + 1])), [noteList]);
  const openOutline = async () => {
    setOutlineOpen(!outlineOpen);
    if (!outline && doc) setOutline(await api.get<OutlineItem[]>(`/api/documents/${doc.id}/outline`));
  };
  const clickMark = async (nid: string) => {
    const n = notes.find((x) => x.id === nid);
    if (n?.mark === "redact") { await api.del(`/api/notes/${nid}`); loadNotes(); } else setActive(nid);
  };

  const segmentFor = (s: Selection): Segment | undefined => {
    if (s.segment_id) return segments.find((x) => x.id === s.segment_id);
    const q = normalize(s.quote);
    const onPage = segments.filter((x) => x.page === s.page);
    return onPage.find((x) => normalize(x.text).includes(q)) ?? onPage.find((x) => q.includes(normalize(x.text).slice(0, 40)));
  };

  const createNote = async (mark: "highlight" | "underline" | "redact" | null, extra: Partial<Note> = {}) => {
    if (!sel || !doc) return;
    const seg = segmentFor(sel);
    const note = await api.post<Note>("/api/notes", {
      document_id: doc.id, mark, color: mark && mark !== "redact" ? color : null, page: sel.page, rects: sel.rects, quote: sel.quote,
      segment_id: seg?.id ?? null, start_offset: sel.start, end_offset: sel.end, body: "", category: mark ? null : "query",
      location: seg?.location ?? (sel.page ? `Page ${sel.page}` : null), ...extra,
    });
    window.getSelection()?.removeAllRanges();
    setSel(null); setMenu(null);
    await loadNotes();
    if (mark !== "redact") setActive(note.id);
  };

  const addStroke = useCallback((page: number, stroke: InkStroke) =>
    setPending((p) => ({ ...p, [page]: [...(p[page] ?? []), stroke] })), []);
  const undoStroke = () => setPending((p) => {
    const pages = Object.keys(p).map(Number).filter((k) => p[k].length);
    const last = pages[pages.length - 1];
    return last ? { ...p, [last]: p[last].slice(0, -1) } : p;
  });
  const finishPen = async () => {
    if (doc) {
      for (const [page, strokes] of Object.entries(pending)) {
        if (!strokes.length) continue;
        const ys = strokes.flatMap((st) => st.points.map((pt) => pt[1]));
        const near = segments.filter((x) => x.page === Number(page) && x.bbox?.length)
          .sort((a, b) => Math.abs(a.bbox[1] - Math.min(...ys)) - Math.abs(b.bbox[1] - Math.min(...ys)))[0];
        await api.post("/api/notes", { document_id: doc.id, mark: "ink", page: Number(page), ink: strokes, quote: "",
          body: "", segment_id: near?.id ?? null, location: near?.location ?? `Page ${page}` });
      }
      await loadNotes();
    }
    setPending({}); setPenOn(false);
  };

  const translate = async (s: Selection) => {
    const seg = segmentFor(s);
    setTr({ x: s.x, y: s.y, data: null });
    try {
      const data = await api.post<Translation>("/api/translate", { text: s.quote, context: seg?.text ?? "" });
      setTr({ x: s.x, y: s.y, data });
    } catch (e) { setTr({ x: s.x, y: s.y, data: null, error: String(e) }); }
  };

  const jumpToSegment = (segmentId: string | null, page?: number) => {
    const seg = segments.find((s) => s.id === segmentId);
    if (seg) setJump({ page: seg.page, box: seg.bbox?.length ? seg.bbox : undefined, segment_id: seg.id, ts: Date.now() });
    else if (page) setJump({ page, ts: Date.now() });
  };
  const jumpToNote = (n: Note) => {
    setActive(n.id);
    if (n.ink?.length && n.page) {
      const xs = n.ink.flatMap((st) => st.points.map((p) => p[0])), ys = n.ink.flatMap((st) => st.points.map((p) => p[1]));
      setJump({ page: n.page, box: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)], ts: Date.now() });
    } else if (n.rects?.length && n.page) {
      const r = n.rects[0];
      setJump({ page: n.page, box: [r[0], r[1], r[0] + r[2], r[1] + r[3]], ts: Date.now() });
    } else jumpToSegment(n.segment_id, n.page ?? undefined);
  };

  const runSearch = () => {
    const q = normalize(search);
    if (!q) return;
    const same = q === hits.q;
    const list = same ? hits.list : segments.filter((x) => normalize(x.text).includes(q));
    const i = same && list.length ? (hits.i + 1) % list.length : 0;
    setHits({ q, list, i });
    if (list[i]) jumpToSegment(list[i].id);
  };

  const upload = async (file: File, revisionOf?: string) => {
    const d = await api.upload<Doc>(`/api/workspaces/${id}/documents`, file, revisionOf ? { revision_of: revisionOf } : {});
    await loadWs();
    setDocId(d.id);
  };

  const onPage = useCallback((n: number, total: number) => setPageInfo([n, total]), []);
  // keep the selection toolbar fully on screen, whatever its width (language, touch sizes)
  const toolbarRef = useRef<HTMLDivElement>(null);
  const [toolbarPos, setToolbarPos] = useState<{ left: number; top: number; visibility?: "hidden" } | null>(null);
  useLayoutEffect(() => {
    if (!sel) { setToolbarPos(null); return; }
    const w = toolbarRef.current?.offsetWidth ?? 460;
    const h = toolbarRef.current?.offsetHeight ?? 44;
    setToolbarPos({ left: Math.max(8, Math.min(window.innerWidth - w - 8, sel.x - w / 2)),
      top: sel.y - h - 12 > 8 ? sel.y - h - 12 : sel.y + 28 });
  }, [sel, lang]);

  return (
    <div className="app" onClick={() => setDocMenu(false)}>
      <header className="topbar">
        <button className="brand" onClick={() => go({ name: "home" })} aria-label={t("home")}><Logo />GrepLens</button>
        <strong title={t("workspace")} style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ws?.name}</strong>
        {ws && <AiPicker ws={ws} onChange={(w) => setWs({ ...ws, ...w, documents: ws.documents })} />}
        <button className="btn" onClick={() => setShowInstructions(true)} title={t("instructionsHint")}>{t("instructionsBtn")}</button>
        <div style={{ position: "relative" }}>
          <button className="btn soft" onClick={(e) => { e.stopPropagation(); setDocMenu(!docMenu); }} disabled={!docs.length}>
            <IconDoc />{doc?.filename ?? t("noDocuments").split(".")[0]}<IconChevron /></button>
          {docMenu && (
            <div className="ctx-menu" style={{ position: "absolute", top: 40, insetInlineStart: 0 }}>
              {docs.map((d) => <button key={d.id} onClick={() => setDocId(d.id)}>{d.filename}{d.revision_of ? " ·  rev." : ""}</button>)}
            </div>
          )}
        </div>
        <input ref={fileRef} type="file" hidden accept=".pdf,.docx,.xlsx,.xlsm,.pptx,.txt,.md,.csv"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
        <input ref={revRef} type="file" hidden accept=".pdf,.docx,.xlsx,.xlsm,.pptx,.txt,.md,.csv"
          onChange={(e) => { const f = e.target.files?.[0]; if (f && doc) upload(f, doc.id); e.target.value = ""; }} />
        <button className="btn" onClick={() => fileRef.current?.click()}><IconPlus />{t("addDocument")}</button>
        {doc && <button className="btn" onClick={() => revRef.current?.click()}>{t("addRevision")}</button>}
        <span className="grow" />
        {doc && <button className="btn" onClick={() => download(`/api/documents/${doc.id}/export.docx`)}><IconDownload />{t("exportWord")}</button>}
        <button className="btn" onClick={() => download(`/api/workspaces/${id}/package`)}><IconShare />{t("sharePkg")}</button>
        <div className="seg" role="group" aria-label={t("language")}>
          <button className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>EN</button>
          <button className={lang === "ar" ? "on" : ""} onClick={() => setLang("ar")}>عربي</button>
        </div>
        <button className="icon-btn" aria-label={t("settings")} onClick={() => go({ name: "settings", back: { name: "workspace", id } })}><IconGear /></button>
      </header>

      <main className="ws-main">
        <section className="doc-pane" aria-label="Document">
          {doc?.status === "ready" && (
            <div className="doc-toolbar">
              <button className={`btn ${outlineOpen ? "soft" : ""}`} aria-expanded={outlineOpen} onClick={openOutline}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" /></svg>
                {t("outline")}</button>
              {doc.kind === "pdf" && <span>{t("page")} <strong>{pageInfo[0]}</strong> / {pageInfo[1] || doc.pages}</span>}
              {doc.kind === "pdf" && !penOn && (
                <button className="btn" onClick={() => { setPenOn(true); setSel(null); }} title={t("penHint")}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></svg>
                  {t("penTool")}</button>
              )}
              {penOn && (
                <div className="pen-bar" role="group" aria-label={t("penTool")}>
                  {["#D92D20", "#1D4F91", "#17202B", "#1F6B3A"].map((c) => (
                    <button key={c} className={`pen-dot ${penColor === c ? "on" : ""}`} style={{ background: c }}
                      aria-label={`${t("penColor")} ${c}`} aria-pressed={penColor === c} onClick={() => setPenColor(c)} />
                  ))}
                  <button className="btn small" onClick={undoStroke}>{t("penUndo")}</button>
                  <button className="btn small primary" onClick={finishPen}>{t("penDone")}</button>
                  <span className="muted small">{t("penHint")}</span>
                </div>
              )}
              <span className="grow" />
              <label className="search-box"><IconSearch /><span className="sr">{t("searchDoc")}</span>
                <input placeholder={t("searchDoc")} value={search} onChange={(e) => setSearch(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && runSearch()} />
                {hits.list.length > 0 && <span className="muted small">{hits.i + 1}/{hits.list.length}</span>}
              </label>
            </div>
          )}
          {outlineOpen && doc?.status === "ready" && (
            <div className="outline-drawer" role="navigation" aria-label={t("outline")}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong>{t("outline")}</strong>
                <button className="icon-btn" aria-label={t("close")} onClick={() => setOutlineOpen(false)}><IconClose /></button>
              </div>
              {!outline && <span className="muted small">…</span>}
              {outline?.length === 0 && <span className="muted small">{t("noOutline")}</span>}
              {outline?.map((o, i) => (
                <div key={i} className="outline-item" style={{ paddingInlineStart: (o.level - 1) * 14 }}>
                  <button className="outline-link" dir="auto" onClick={() => jumpToSegment(o.segment_ids[0] ?? null, o.page)}>
                    <span>{o.label}{o.title ? ` · ${o.title}` : ""}</span><span className="muted small">{o.page}</span></button>
                  {o.segment_ids.length > 0 && (
                    <button className="btn small" title={t("summarizeSection")} onClick={() =>
                      setSectionRequest({ segment_ids: o.segment_ids, title: o.title ? `${o.label} · ${o.title}` : o.label, ts: Date.now() })}>
                      {t("summarizeShort")}</button>)}
                </div>
              ))}
            </div>
          )}
          {!doc && <div className="card muted" style={{ maxWidth: 560, marginTop: 40 }}>{t("noDocuments")}</div>}
          {doc && doc.status !== "ready" && (
            <div className="card col" style={{ width: "100%", maxWidth: 560, marginTop: 40 }}>
              <strong>{doc.status === "error" ? t("error") : t("preparing")}</strong>
              {doc.status !== "error" && <div className="bar"><div style={{ width: `${doc.progress * 100}%` }} /></div>}
              <span className="muted small">{doc.message}</span>
            </div>
          )}
          {doc?.status === "ready" && doc.kind === "pdf" && (
            <PdfView url={`/api/documents/${doc.id}/file`} notes={notes} numbers={numbers} activeNote={active} jump={jump}
              onSelect={handleSelect} onNoteClick={clickMark} onPage={onPage}
              onContext={(s, x, y) => { setSel(s); setMenu({ x, y }); }}
              pen={{ on: penOn, color: penColor, width: 2.5, pending, add: addStroke }} />
          )}
          {doc?.status === "ready" && doc.kind !== "pdf" && (
            <TextView segments={segments} notes={notes} numbers={numbers} activeNote={active} jump={jump}
              onSelect={handleSelect} onNoteClick={(nid) => setActive(nid)}
              onContext={(s, x, y) => { setSel(s); setMenu({ x, y }); }} />
          )}
        </section>

        <NotesPanel notes={noteList} numbers={numbers} active={active} onActive={setActive} onJump={jumpToNote} reload={loadNotes}
          onAdd={doc?.status === "ready" ? async () => {
            const page = doc.kind === "pdf" ? pageInfo[0] : 1;
            const near = segments.find((x) => x.page === page);
            const n = await api.post<Note>("/api/notes", { document_id: doc.id, page, category: "info", body: "",
              segment_id: near?.id ?? null, location: near?.location ?? `Page ${page}` });
            await loadNotes(); setActive(n.id);
          } : undefined} />
        <AssistantPanel doc={doc} workspace={ws} segments={segments} notes={notes} activeNote={active}
          onJump={jumpToSegment} reloadNotes={loadNotes} prefill={askPrefill}
          page={doc?.kind === "pdf" ? pageInfo[0] : 1} sectionRequest={sectionRequest} redactCount={redactCount}
          onOpenMindMap={setMindMap} onOpenQuiz={setQuiz} />
      </main>

      <footer className="statusbar">
        <span>{doc ? `${doc.filename} · ${doc.pages} ${t("page").toLowerCase()}` : ""}</span>
        {doc && doc.ocr_pages > 0 && <span>OCR: {doc.ocr_pages} · GrepAll</span>}
        <span>{noteList.length} {t("notesCount")}</span>
      </footer>

      {sel && !menu && !penOn && (
        <div ref={toolbarRef} className="sel-toolbar" style={toolbarPos ?? { left: 0, top: 0, visibility: "hidden" }} onPointerUp={(e) => e.stopPropagation()}>
          <button className={`tb icon ${tool === "highlight" ? "on" : ""}`} aria-label={t("highlight")} title={t("highlight")}
            onClick={() => { setTool("highlight"); createNote("highlight"); }}><IconHighlight /></button>
          <button className={`tb icon ${tool === "underline" ? "on" : ""}`} aria-label={t("underline")} title={t("underline")}
            onClick={() => { setTool("underline"); createNote("underline"); }}><IconUnderline /></button>
          <span className="sep" />
          {COLORS.map((c) => (
            <button key={c} className="dot-btn" aria-label={c} aria-pressed={color === c} onClick={() => setColor(c)}>
              <span style={tool === "underline"
                ? { width: 18, height: 0, borderBottom: `4px solid ${colorOf(c)}`, outline: color === c ? "2px solid #fff" : undefined, outlineOffset: 3 }
                : { width: 18, height: 18, borderRadius: "50%", background: colorOf(c), boxShadow: color === c ? "0 0 0 2px #17202B, 0 0 0 4px #fff" : "none" }} />
            </button>
          ))}
          <span className="sep" />
          <button className="tb" onClick={() => createNote(null)}>{t("addNote")}</button>
          <button className="tb" onClick={() => { setAskPrefill({ text: sel.quote, ts: Date.now() }); setSel(null); }}>{t("askAi")}</button>
          <button className="tb accent" onClick={() => { translate(sel); setSel(null); }}>{t("translate")}</button>
          {doc?.kind === "pdf" && sel.rects.length > 0 && <button className="tb" title={t("redactHint")} onClick={() => createNote("redact")}>{t("redact")}</button>}
        </div>
      )}

      {menu && sel && (
        <div className="ctx-menu" style={{ left: Math.min(menu.x, window.innerWidth - 210), top: Math.min(menu.y, window.innerHeight - 220) }}
          onMouseLeave={() => setMenu(null)}>
          <button onClick={() => { translate(sel); setMenu(null); setSel(null); }}>{t("translate")}</button>
          <button onClick={() => createNote("highlight")}>{t("highlight")}</button>
          <button onClick={() => createNote("underline")}>{t("underline")}</button>
          <button onClick={() => createNote(null)}>{t("addNote")}</button>
          <button onClick={() => { setAskPrefill({ text: sel.quote, ts: Date.now() }); setMenu(null); setSel(null); }}>{t("askAi")}</button>
          <button onClick={() => { navigator.clipboard?.writeText(sel.quote); setMenu(null); }}>{t("copy")}</button>
          {doc?.kind === "pdf" && sel.rects.length > 0 && <button onClick={() => createNote("redact")}>{t("redact")}</button>}
        </div>
      )}

      {showInstructions && <InstructionsDialog workspaceId={id} onClose={() => setShowInstructions(false)} />}
      {mindMap && <MindMapView map={mindMap} onJump={jumpToSegment} onClose={() => setMindMap(null)} />}
      {quiz && <QuizView questions={quiz} onJump={jumpToSegment} onClose={() => setQuiz(null)} />}
      {tr && (
        <div className="popup" role="dialog" aria-label={t("translation")}
          style={{ left: Math.min(window.innerWidth - 336, Math.max(8, tr.x - 160)), top: Math.min(window.innerHeight - 260, tr.y + 16) }}>
          <div className="popup-head">
            <span>{tr.data ? (tr.data.source === "en" ? "English → العربية" : "العربية → English") : t("translation")}</span>
            <button className="icon-btn" aria-label={t("close")} onClick={() => setTr(null)}><IconClose /></button>
          </div>
          <div className="col" style={{ padding: "12px 14px" }}>
            {!tr.data && !tr.error && <span className="muted">…</span>}
            {tr.error && <span className="error-box small">{tr.error}</span>}
            {tr.data && (
              <>
                <span className="muted" dir="auto">{tr.data.text}</span>
                <span dir="auto" lang={tr.data.target} style={{ fontSize: 21, fontWeight: 600 }}>{tr.data.translation}</span>
                {tr.data.glossary && <span className="small" style={{ color: "var(--accent-dark)" }}>{t("glossaryTerm")}</span>}
                {tr.data.note && <span className="muted small" dir="auto">{tr.data.note}</span>}
                <div className="row">
                  <button className="btn primary small grow" onClick={async () => {
                    if (!doc) return;
                    await api.post("/api/notes", { document_id: doc.id, quote: tr.data!.text, body: `${t("translation")}: ${tr.data!.translation}`,
                      category: "info", page: pageInfo[0] });
                    loadNotes(); setTr(null);
                  }}>{t("saveAsNote")}</button>
                  <button className="btn small" onClick={() => navigator.clipboard?.writeText(tr.data!.translation)}>{t("copy")}</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
