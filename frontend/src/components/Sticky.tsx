import { useEffect, useRef, useState } from "react";
import { api, Note } from "../api";
import { Key, useI18n } from "../i18n";
import { notesApi } from "../undo";
import { HandwritingView } from "./Handwriting";
import { colorOf } from "./PdfView";

const CARD_W = 250;

type Props = {
  note: Note; number?: number; pageW: number; pageH: number; anchor: { x: number; y: number }; active: boolean;
  onOpen: () => void; onClose: () => void; onMore: () => void; reload: () => void;
};

/**
 * A note shown as a sticky note on the PDF page. Open, it floats beside its text and can be dragged anywhere;
 * hidden, it leaves a small note icon next to the text it belongs to.
 */
export function StickyNote({ note: n, number, pageW, pageH, anchor, active, onOpen, onClose, onMore, reload }: Props) {
  const { t } = useI18n();
  const open = active || !!n.sticky?.open;
  const saved = n.sticky ?? null;
  const fallback = { x: Math.min(anchor.x + 0.02, Math.max(0, 1 - (CARD_W + 8) / pageW)), y: Math.min(anchor.y + 0.02, 0.9) };
  const [pos, setPos] = useState(saved ? { x: saved.x, y: saved.y } : fallback);
  useEffect(() => { if (saved) setPos({ x: saved.x, y: saved.y }); }, [saved?.x, saved?.y]); // eslint-disable-line react-hooks/exhaustive-deps
  const [body, setBody] = useState(n.body ?? "");
  useEffect(() => setBody(n.body ?? ""), [n.body]);
  const drag = useRef<{ x0: number; y0: number; px: number; py: number; moved: boolean } | null>(null);

  const keep = (next: Partial<{ x: number; y: number; open: boolean }>) =>
    api.patch(`/api/notes/${n.id}`, { sticky: { x: pos.x, y: pos.y, open, ...next } }).then(reload);

  if (!open) {
    // the small icon beside the text; a tap opens the sticky note
    return (
      <button className={`sticky-icon ${n.category ?? ""}`} style={{ left: anchor.x * pageW, top: anchor.y * pageH }}
        aria-label={`${t("openSticky")} ${number ?? ""}`} title={(n.body || n.handwriting_text || n.quote || "").slice(0, 120)}
        onClick={(e) => { e.stopPropagation(); onOpen(); }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round" aria-hidden="true">
          <path d="M4 4h16v11l-5 5H4z" /><path d="M15 20v-5h5" /></svg>
        {number != null && <span>{number}</span>}
      </button>
    );
  }
  return (
    <div className={`sticky ${active ? "active" : ""}`} style={{ left: pos.x * pageW, top: pos.y * pageH, width: CARD_W }}
      onClick={(e) => e.stopPropagation()} onPointerUp={(e) => e.stopPropagation()}>
      <div className="sticky-head" title={t("dragSticky")}
        onPointerDown={(e) => {
          if ((e.target as HTMLElement).closest("button")) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          drag.current = { x0: e.clientX, y0: e.clientY, px: pos.x, py: pos.y, moved: false };
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d) return;
          const page = (e.currentTarget.closest(".page-wrap") as HTMLElement)?.getBoundingClientRect();
          if (!page) return;
          const dx = (e.clientX - d.x0) / page.width, dy = (e.clientY - d.y0) / page.height;
          if (Math.abs(e.clientX - d.x0) + Math.abs(e.clientY - d.y0) > 3) d.moved = true;
          setPos({ x: Math.min(1 - 40 / pageW, Math.max(-0.05, d.px + dx)), y: Math.min(0.98, Math.max(0, d.py + dy)) });
        }}
        onPointerUp={() => { if (drag.current?.moved) keep({}); drag.current = null; }}>
        <span className="note-num" style={{ background: n.mark === "ink" ? "#FDECEA" : n.mark ? colorOf(n.color) : "var(--chip)" }}>{number}</span>
        {n.category && <span className={`pill ${n.category}`}>{t(n.category as Key)}</span>}
        <span className="grow" />
        <button className="sticky-btn" aria-label={t("moreSticky")} title={t("moreSticky")} onClick={onMore}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" /></svg></button>
        <button className="sticky-btn" aria-label={t("hideSticky")} title={t("hideSticky")}
          onClick={() => { keep({ open: false }); onClose(); }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
            <path d="M5 12h14" /></svg></button>
      </div>
      {n.quote && <div className="sticky-quote" dir="auto">“{n.quote.length > 90 ? n.quote.slice(0, 90) + "…" : n.quote}”</div>}
      <textarea dir="auto" value={body} placeholder={t("notePlaceholder")} autoFocus={active && !n.body} rows={Math.min(8, Math.max(3, body.split("\n").length))}
        onFocus={onOpen} onChange={(e) => setBody(e.target.value)}
        onBlur={() => { if (body !== (n.body ?? "")) notesApi.patch(n, { body }).then(reload); }} />
      {n.handwriting?.length ? <HandwritingView strokes={n.handwriting} /> : null}
    </div>
  );
}

/** Where a note's icon sits: just after the end of its first highlighted line, or beside its ink. */
export function stickyAnchor(n: Note, fallbackY: number): { x: number; y: number } {
  if (n.rects?.length) {
    const r = n.rects[0];
    return { x: Math.min(0.97, r[0] + r[2] + 0.006), y: Math.max(0, r[1] + r[3] / 2 - 0.009) };
  }
  if (n.ink?.length) {
    const xs = n.ink.flatMap((s) => s.points.map((p) => p[0])), ys = n.ink.flatMap((s) => s.points.map((p) => p[1]));
    return { x: Math.min(0.97, Math.max(...xs) + 0.01), y: Math.min(...ys) };
  }
  if (n.sticky) return { x: n.sticky.x, y: n.sticky.y };
  return { x: 0.94, y: fallbackY };
}
