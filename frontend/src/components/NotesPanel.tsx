import { useEffect, useRef, useState } from "react";
import { api, Note } from "../api";
import { Key, useI18n } from "../i18n";
import { IconTrash } from "../icons";
import { colorOf } from "./PdfView";
import type { InkStroke } from "../api";

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
  onJump: (n: Note) => void; reload: () => void };

export default function NotesPanel({ notes, numbers, active, onActive, onJump, reload }: Props) {
  const { t } = useI18n();
  const [filter, setFilter] = useState("all");
  const [status, setStatus] = useState("all");
  const refs = useRef<Record<string, HTMLDivElement | null>>({});
  useEffect(() => { if (active) refs.current[active]?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [active]);

  const shown = notes.filter((n) => (filter === "all" || n.category === filter) && (status === "all" || n.status === status));
  const patch = async (n: Note, body: Partial<Note>) => { await api.patch(`/api/notes/${n.id}`, body); reload(); };

  return (
    <aside className="notes-pane" aria-label={t("notes")}>
      <h3 style={{ fontSize: 15 }}>{t("notes")} <span className="muted" style={{ fontWeight: 500 }}>· {shown.length}</span></h3>
      <div className="row wrap" style={{ gap: 6 }}>
        {["all", ...CATS.slice(0, 3)].map((c) => (
          <button key={c} className={`chip solid small ${filter === c ? "on" : ""}`} onClick={() => setFilter(c)}>{t(c as Key)}</button>
        ))}
        <select className="input" style={{ minHeight: 32, width: "auto", padding: "0 8px" }} value={status} aria-label="Status"
          onChange={(e) => setStatus(e.target.value)}>
          <option value="all">{t("all")}</option><option value="open">{t("open")}</option><option value="resolved">{t("resolved")}</option>
        </select>
      </div>
      {notes.length === 0 && <div className="muted small">{t("noNotes")}</div>}
      {shown.map((n) => (
        <div key={n.id} ref={(el) => { refs.current[n.id] = el; }} className={`note-card ${active === n.id ? "active" : ""}`}
          onClick={() => onActive(n.id)}>
          <div className="row wrap" style={{ gap: 6 }}>
            <span className="note-num" style={{ background: n.mark === "ink" ? "#FDECEA" : n.mark ? colorOf(n.color) : "#EAEEF2" }}>{numbers[n.id]}</span>
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
          <TagEditor tags={n.tags ?? []} onSave={(tags) => patch(n, { tags })} />
          <div className="row wrap muted small" style={{ justifyContent: "space-between" }}>
            <span dir="auto">{n.location}</span>
            <span>{n.author} · {new Date(n.created * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}</span>
          </div>
          <div className="row" style={{ gap: 6 }}>
            <button className="btn soft small" onClick={(e) => { e.stopPropagation(); onJump(n); }}>{t("jump")}</button>
            <button className="btn small" onClick={(e) => { e.stopPropagation(); patch(n, { status: n.status === "open" ? "resolved" : "open" }); }}>
              {n.status === "open" ? t("resolve") : t("reopen")}</button>
          </div>
        </div>
      ))}
    </aside>
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
