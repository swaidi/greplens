import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, Doc, download, normalize, Note, Segment, Workspace } from "../api";
import { useI18n } from "../i18n";
import { IconChevron, IconClose, IconDoc, IconDownload, IconGear, IconHighlight, IconPlus, IconSearch, IconShare, IconUnderline, Logo } from "../icons";
import type { View } from "../App";
import PdfView, { colorOf } from "./PdfView";
import TextView from "./TextView";
import NotesPanel from "./NotesPanel";
import AssistantPanel from "./AssistantPanel";

export type Selection = { page: number; rects: number[][]; quote: string; segment_id: string | null; start: number | null;
  end: number | null; x: number; y: number };
export type Jump = { page?: number; box?: number[]; segment_id?: string; ts: number };
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

  const loadNotes = useCallback(() => { if (docId) api.get<Note[]>(`/api/documents/${docId}/notes`).then(setNotes); }, [docId]);
  useEffect(() => {
    setSel(null); setActive(null); setSegments([]); setNotes([]);
    if (doc?.status === "ready") { api.get<Segment[]>(`/api/documents/${doc.id}/segments`).then(setSegments); loadNotes(); }
  }, [docId, doc?.status, loadNotes]); // eslint-disable-line react-hooks/exhaustive-deps

  const numbers = useMemo(() => Object.fromEntries(notes.map((n, i) => [n.id, i + 1])), [notes]);

  const segmentFor = (s: Selection): Segment | undefined => {
    if (s.segment_id) return segments.find((x) => x.id === s.segment_id);
    const q = normalize(s.quote);
    const onPage = segments.filter((x) => x.page === s.page);
    return onPage.find((x) => normalize(x.text).includes(q)) ?? onPage.find((x) => q.includes(normalize(x.text).slice(0, 40)));
  };

  const createNote = async (mark: "highlight" | "underline" | null, extra: Partial<Note> = {}) => {
    if (!sel || !doc) return;
    const seg = segmentFor(sel);
    const note = await api.post<Note>("/api/notes", {
      document_id: doc.id, mark, color: mark ? color : null, page: sel.page, rects: sel.rects, quote: sel.quote,
      segment_id: seg?.id ?? null, start_offset: sel.start, end_offset: sel.end, body: "", category: mark ? null : "query",
      location: seg?.location ?? (sel.page ? `Page ${sel.page}` : null), ...extra,
    });
    window.getSelection()?.removeAllRanges();
    setSel(null); setMenu(null);
    await loadNotes();
    setActive(note.id);
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
    if (n.rects?.length && n.page) {
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
  const toolbarPos = sel ? { left: Math.min(window.innerWidth - 440, Math.max(8, sel.x - 180)), top: Math.max(8, sel.y - 64) } : null;

  return (
    <div className="app" onClick={() => setDocMenu(false)}>
      <header className="topbar">
        <button className="brand" onClick={() => go({ name: "home" })} aria-label={t("home")}><Logo />GrepLens</button>
        <strong title={t("workspace")} style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{ws?.name}</strong>
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
              {doc.kind === "pdf" && <span>{t("page")} <strong>{pageInfo[0]}</strong> / {pageInfo[1] || doc.pages}</span>}
              <span className="grow" />
              <label className="search-box"><IconSearch /><span className="sr">{t("searchDoc")}</span>
                <input placeholder={t("searchDoc")} value={search} onChange={(e) => setSearch(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && runSearch()} />
                {hits.list.length > 0 && <span className="muted small">{hits.i + 1}/{hits.list.length}</span>}
              </label>
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
              onSelect={(s) => { setSel(s); setMenu(null); }} onNoteClick={(nid) => setActive(nid)} onPage={onPage}
              onContext={(s, x, y) => { setSel(s); setMenu({ x, y }); }} />
          )}
          {doc?.status === "ready" && doc.kind !== "pdf" && (
            <TextView segments={segments} notes={notes} numbers={numbers} activeNote={active} jump={jump}
              onSelect={(s) => { setSel(s); setMenu(null); }} onNoteClick={(nid) => setActive(nid)}
              onContext={(s, x, y) => { setSel(s); setMenu({ x, y }); }} />
          )}
        </section>

        <NotesPanel notes={notes} numbers={numbers} active={active} onActive={setActive} onJump={jumpToNote} reload={loadNotes} />
        <AssistantPanel doc={doc} workspace={ws} segments={segments} notes={notes} activeNote={active}
          onJump={jumpToSegment} reloadNotes={loadNotes} prefill={askPrefill} />
      </main>

      <footer className="statusbar">
        <span>{doc ? `${doc.filename} · ${doc.pages} ${t("page").toLowerCase()}` : ""}</span>
        {doc && doc.ocr_pages > 0 && <span>OCR: {doc.ocr_pages} · GrepAll</span>}
        <span>{notes.length} {t("notesCount")}</span>
      </footer>

      {sel && toolbarPos && !menu && (
        <div className="sel-toolbar" style={toolbarPos} onMouseUp={(e) => e.stopPropagation()}>
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
        </div>
      )}

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
