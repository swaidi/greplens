import { useEffect, useRef, useState } from "react";
import { api, normalize, Note } from "../api";
import { Key, useI18n } from "../i18n";
import { IconTrash } from "../icons";
import { colorOf } from "./PdfView";
import type { InkStroke } from "../api";
import { HandwritingPad, HandwritingView } from "./Handwriting";

function InkPreview({ ink }: { ink: InkStroke[] }) {
  const xs = ink.flatMap((s) => s.points.map((p) => p[0])), ys = ink.flatMap((s) => s.points.map((p) => p[1]));
  const [x0, y0] = [Math.min(...xs), Math.min(...ys)];
  const w = Math.max(0.01, Math.max(...xs) - x0), h = Math.max(0.01, Math.max(...ys) - y0);
  const k = 280 / Math.max(w, h * 2);
  return (
    <svg width="100%" height={Math.min(120, h * k + 16)} viewBox={`-8 -8 ${w * k + 16} ${h * k + 16}`} role="img" aria-label="Ink note"
      style={{ background: "#F6F8FA", borderRadius: 6 }}>
      {ink.map((s, i) => <path key={i} d={s.points.map((p, j) => `${j ? "L" : "M"}${((p[0] - x0) * k).toFixed(1)} ${((p[1] - y0) * k).toFixed(1)}`).join(" ")}
        stroke={s.color} strokeWidth={2} fill="none" strokeLinecap="round" strokeLinejoin="round" />)}
    </svg>
  );
}

const CATS = ["risk", "query", "clarification", "action", "info"];

type Props = { notes: Note[]; numbers: Record<string, number>; active: string | null; onActive: (id: string) => void;
  onJump: (n: Note) => void; reload: () => void; onAdd?: () => void; workspaceId?: string; documentId?: string;
  onOpenNote?: (n: Note) => void };
type HwStatus = { available: boolean; languages: string[] };
let hwStatus: Promise<HwStatus> | null = null;

const haystack = (n: Note) => normalize([n.body, n.quote, n.handwriting_text, n.location, n.category, ...(n.tags ?? [])].join(" "));

export default function NotesPanel({ notes, numbers, active, onActive, onJump, reload, onAdd, workspaceId, documentId, onOpenNote }: Props) {
  const { t } = useI18n();
  const [writing, setWriting] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<"doc" | "ws">("doc");
  const [wsHits, setWsHits] = useState<Note[] | null>(null);
  const [hwErrors, setHwErrors] = useState<Record<string, string>>({});
  const [hw, setHw] = useState<HwStatus | null>(null);
  useEffect(() => { (hwStatus ??= api.get<HwStatus>("/api/handwriting/status")).then(setHw).catch(() => setHw({ available: false, languages: [] })); }, []);
  const words = normalize(query).split(" ").filter(Boolean);
  useEffect(() => {
    if (scope !== "ws" || !words.length || !workspaceId) { setWsHits(null); return; }
    const timer = setTimeout(() => api.get<Note[]>(`/api/workspaces/${workspaceId}/notes/search?q=${encodeURIComponent(query)}`)
      .then(setWsHits).catch(() => setWsHits([])), 250);
    return () => clearTimeout(timer);
  }, [query, scope, workspaceId, documentId]); // eslint-disable-line react-hooks/exhaustive-deps
  const saveHandwriting = async (n: Note, strokes: InkStroke[], lang: string) => {
    const r = await api.patch<Note>(`/api/notes/${n.id}`, { handwriting: strokes, handwriting_lang: lang });
    setHwErrors((e) => ({ ...e, [n.id]: strokes.length ? r.handwriting_error ?? "" : "" }));
    reload();
  };
  const readAgain = async (n: Note, lang: string) => {
    try { await api.post(`/api/notes/${n.id}/read-handwriting`, { lang }); setHwErrors((e) => ({ ...e, [n.id]: "" })); }
    catch (err) { setHwErrors((e) => ({ ...e, [n.id]: String(err).replace(/^Error: /, "") })); }
    reload();
  };
  const [filter, setFilter] = useState("all");
  const [status, setStatus] = useState("all");
  const refs = useRef<Record<string, HTMLDivElement | null>>({});
  useEffect(() => { if (active) refs.current[active]?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [active]);

  const shown = notes.filter((n) => (filter === "all" || n.category === filter) && (status === "all" || n.status === status)
    && (!words.length || words.every((w) => haystack(n).includes(w))));
  const patch = async (n: Note, body: Partial<Note>) => { await api.patch(`/api/notes/${n.id}`, body); reload(); };

  return (
    <aside className="notes-pane" aria-label={t("notes")}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ fontSize: 15 }}>{t("notes")} <span className="muted" style={{ fontWeight: 500 }}>· {shown.length}</span></h3>
        {onAdd && <button className="icon-btn" aria-label={t("pageNote")} title={t("pageNote")}
          onClick={onAdd} style={{ border: "1px solid var(--line)", fontSize: 18 }}>+</button>}
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        {["all", ...CATS.slice(0, 3)].map((c) => (
          <button key={c} className={`chip solid small ${filter === c ? "on" : ""}`} onClick={() => setFilter(c)}>{t(c as Key)}</button>
        ))}
        <select className="input" style={{ minHeight: 32, width: "auto", padding: "0 8px" }} value={status} aria-label="Status"
          onChange={(e) => setStatus(e.target.value)}>
          <option value="all">{t("all")}</option><option value="open">{t("open")}</option><option value="resolved">{t("resolved")}</option>
        </select>
      </div>
      <div className="col" style={{ gap: 6 }}>
        <input className="input" type="search" dir="auto" placeholder={t("searchNotes")} aria-label={t("searchNotes")} value={query}
          onChange={(e) => setQuery(e.target.value)} style={{ minHeight: 36 }} />
        {words.length > 0 && workspaceId && (
          <div className="seg" role="group" aria-label={t("scope")} style={{ alignSelf: "flex-start" }}>
            <button className={scope === "doc" ? "on" : ""} onClick={() => setScope("doc")}>{t("scopeDoc")}</button>
            <button className={scope === "ws" ? "on" : ""} onClick={() => setScope("ws")}>{t("scopeWs")}</button>
          </div>)}
      </div>
      {notes.length === 0 && !wsHits && <div className="muted small">{t("noNotes")}</div>}
      {words.length > 0 && ((scope === "doc" && !shown.length) || wsHits?.length === 0) && <div className="muted small">{t("noMatches")}</div>}
      {wsHits?.map((n) => (
        <button key={n.id} className="note-card hit" onClick={() => onOpenNote?.(n)}>
          <span className="row wrap small" style={{ gap: 6 }}>
            <strong style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%" }}>{n.document_name}</strong>
            <span className="muted" dir="auto">{n.location}</span>
          </span>
          {n.quote && <span className="quote" dir="auto">“{n.quote.slice(0, 140)}”</span>}
          {n.body && <span dir="auto">{n.body.slice(0, 200)}</span>}
          {n.handwriting_text && <span className="muted small" dir="auto">{t("readsAs")}: {n.handwriting_text}</span>}
        </button>
      ))}
      {!wsHits && shown.map((n) => (
        <div key={n.id} ref={(el) => { refs.current[n.id] = el; }} className={`note-card ${active === n.id ? "active" : ""}`}
          onClick={() => onActive(n.id)}>
          <div className="row wrap" style={{ gap: 6 }}>
            <span className="note-num" style={{ background: n.mark === "ink" ? "#FDECEA" : n.mark ? colorOf(n.color) : "var(--chip)" }}>{numbers[n.id]}</span>
            {n.mark === "ink" && <span className="pill high">{t("inkNote")}</span>}
            <select className={`pill ${n.category ?? ""}`} style={{ border: 0 }} value={n.category ?? ""} aria-label="Category"
              onChange={(e) => patch(n, { category: e.target.value || null })}>
              <option value="">—</option>
              {CATS.map((c) => <option key={c} value={c}>{t(c as Key)}</option>)}
            </select>
            <span className={`pill ${n.status === "open" ? "open" : ""}`}>{n.status === "open" ? t("open") : t("resolved")}</span>
            {!!n.ai_answer && <span className="pill ai">{t("aiAnswer")}</span>}
            <span className="grow" />
            <button className="icon-btn" style={{ width: 30, height: 30 }} aria-label={t("delete")}
              onClick={async (e) => { e.stopPropagation(); await api.del(`/api/notes/${n.id}`); reload(); }}><IconTrash /></button>
          </div>
          {n.mark === "ink" && n.ink?.length ? <InkPreview ink={n.ink} /> : null}
          {n.quote && <div className="quote" dir="auto">“{n.quote.length > 220 ? n.quote.slice(0, 220) + "…" : n.quote}”</div>}
          <NoteBody note={n} onSave={(body) => patch(n, { body })} />
          {writing === n.id
            ? <HandwritingPad initial={n.handwriting ?? []} initialLang={n.handwriting_lang} onCancel={() => setWriting(null)}
                onSave={async (strokes, lang) => { setWriting(null); await saveHandwriting(n, strokes, lang); }} />
            : n.handwriting?.length ? (
              <>
                <div onDoubleClick={() => setWriting(n.id)}><HandwritingView strokes={n.handwriting} /></div>
                <HandwritingText note={n} status={hw} error={hwErrors[n.id] ?? ""} onSave={(text) => patch(n, { handwriting_text: text })}
                  onRead={(lang) => readAgain(n, lang)} />
              </>) : null}
          <TagEditor tags={n.tags ?? []} onSave={(tags) => patch(n, { tags })} />
          <div className="row wrap muted small" style={{ justifyContent: "space-between" }}>
            <span dir="auto">{n.location}</span>
            <span>{n.author} · {new Date(n.created * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}</span>
          </div>
          <div className="row wrap" style={{ gap: 6 }}>
            <button className="btn soft small" onClick={(e) => { e.stopPropagation(); onJump(n); }}>{t("jump")}</button>
            <button className="btn small" onClick={(e) => { e.stopPropagation(); patch(n, { status: n.status === "open" ? "resolved" : "open" }); }}>
              {n.status === "open" ? t("resolve") : t("reopen")}</button>
            {writing !== n.id && <button className="btn small" onClick={(e) => { e.stopPropagation(); setWriting(n.id); }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></svg>
              {t("handwrite")}</button>}
          </div>
        </div>
      ))}
    </aside>
  );
}

function HandwritingText({ note, status, error, onSave, onRead }: { note: Note; status: HwStatus | null; error: string;
  onSave: (text: string) => void; onRead: (lang: string) => Promise<void> }) {
  const { t } = useI18n();
  const [value, setValue] = useState(note.handwriting_text ?? "");
  const [busy, setBusy] = useState(false);
  useEffect(() => setValue(note.handwriting_text ?? ""), [note.handwriting_text]);
  const other = note.handwriting_lang === "ar" ? "en" : "ar";
  if (status && !status.available) return <span className="muted small">{t("hwUnavailable")}</span>;
  return (
    <div className="col" style={{ gap: 4 }} onClick={(e) => e.stopPropagation()}>
      <label className="row small" style={{ alignItems: "center" }}><span className="muted" style={{ whiteSpace: "nowrap" }}>{t("readsAs")}</span>
        <input className="input" dir="auto" style={{ minHeight: 32, padding: "4px 8px" }} value={value} placeholder={busy ? t("hwReading") : "—"}
          title={t("readsAsHint")} onChange={(e) => setValue(e.target.value)}
          onBlur={() => value !== (note.handwriting_text ?? "") && onSave(value)} /></label>
      <div className="row wrap small" style={{ gap: 6 }}>
        <button className="btn small" disabled={busy} onClick={async () => { setBusy(true); await onRead(note.handwriting_lang || "en"); setBusy(false); }}>
          {t("readAgain")}</button>
        <button className="btn small" disabled={busy} onClick={async () => { setBusy(true); await onRead(other); setBusy(false); }}>
          {t(other === "ar" ? "readInArabic" : "readInEnglish")}</button>
      </div>
      {error && <span className="error-box small">{t("hwReadFailed")} {error}</span>}
    </div>
  );
}

function NoteBody({ note, onSave }: { note: Note; onSave: (body: string) => void }) {
  const { t } = useI18n();
  const [value, setValue] = useState(note.body ?? "");
  useEffect(() => setValue(note.body ?? ""), [note.body]);
  return (
    <textarea className="input" dir="auto" rows={Math.min(8, Math.max(2, value.split("\n").length))} value={value}
      placeholder={t("notePlaceholder")} onChange={(e) => setValue(e.target.value)}
      onBlur={() => value !== (note.body ?? "") && onSave(value)} />
  );
}

function TagEditor({ tags, onSave }: { tags: string[]; onSave: (tags: string[]) => void }) {
  const { t } = useI18n();
  const [value, setValue] = useState(tags.map((x) => `#${x}`).join(" "));
  useEffect(() => setValue(tags.map((x) => `#${x}`).join(" ")), [tags]);
  const parse = (v: string) => v.split(/[\s,]+/).map((x) => x.replace(/^#/, "").trim()).filter(Boolean);
  return (
    <label className="row small"><span className="muted">{t("tags")}</span>
      <input className="input" style={{ minHeight: 30, padding: "4px 8px" }} value={value} placeholder="#tag"
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => { const next = parse(value); if (next.join() !== tags.join()) onSave(next); }} /></label>
  );
}
