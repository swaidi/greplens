import { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { InkStroke, Note } from "../api";
import type { Jump, Selection } from "./Workspace";
import { isScratch, strokesHit } from "../gestures";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
const COLORS: Record<string, string> = { yellow: "#FFE58A", green: "#BDEBC8", blue: "#BFDDFB", pink: "#F9C9DD", orange: "#FFC999" };
export type PenMode = "draw" | "highlight" | "select";
export const colorOf = (c: string | null) => COLORS[c ?? "yellow"] ?? COLORS.yellow;

type Props = {
  url: string; notes: Note[]; numbers: Record<string, number>; activeNote: string | null; jump: Jump | null;
  onSelect: (s: Selection | null) => void; onNoteClick: (id: string) => void; onPage: (n: number, total: number) => void;
  onContext: (s: Selection, x: number, y: number) => void;
  pen: { on: boolean; color: string; width: number; pending: Record<number, InkStroke[]>; add: (page: number, stroke: InkStroke) => void;
    /** remove ink a scratch-out or the pen's eraser touched: pending strokes by index, saved ink notes by stroke index */
    erase: (page: number, pending: number[], saved: { note: Note; strokes: number[] }[]) => void;
    /** what a pen does when it touches the page with the Pen tool off: draw ink, highlight text, or select text */
    mode: PenMode; highlightColor: string; onHighlight: (s: Selection) => void };
  /** room kept beside each page for margin notes (pixels) */
  reserve?: number;
  renderMargin?: (page: number, height: number) => React.ReactNode;
  renderOverlay?: (page: number, height: number, width: number) => React.ReactNode;
};

export default function PdfView({ url, notes, numbers, activeNote, jump, onSelect, onNoteClick, onPage, onContext, pen, reserve = 0,
  renderMargin, renderOverlay }: Props) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [sizes, setSizes] = useState<{ w: number; h: number }[]>([]);
  const [width, setWidth] = useState(820);
  const [error, setError] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    let cancelled = false;
    const task = pdfjs.getDocument({ url, cMapUrl: "/pdfjs/cmaps/", cMapPacked: true, standardFontDataUrl: "/pdfjs/standard_fonts/" });
    task.promise.then(async (doc) => {
      const list = [];
      for (let i = 1; i <= doc.numPages; i++) {
        const vp = (await doc.getPage(i)).getViewport({ scale: 1 });
        list.push({ w: vp.width, h: vp.height });
      }
      if (!cancelled) { setPdf(doc); setSizes(list); }
    }).catch((e) => setError(String(e)));
    return () => { cancelled = true; task.destroy(); };
  }, [url]);

  useEffect(() => {
    const el = boxRef.current?.parentElement;
    if (!el) return;
    const fit = () => setWidth(Math.min(1000, Math.max(300, el.clientWidth - 64 - reserve)));
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    fit();
    return () => ro.disconnect();
  }, [reserve]);

  // report the page in view
  useEffect(() => {
    const root = boxRef.current?.parentElement;
    if (!root || !sizes.length) return;
    const io = new IntersectionObserver((entries) => {
      const best = entries.filter((e) => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (best) onPage(Number((best.target as HTMLElement).dataset.page), sizes.length);
    }, { root, threshold: [0.25, 0.5, 0.75] });
    pageRefs.current.forEach((p) => p && io.observe(p));
    return () => io.disconnect();
  }, [sizes, onPage]);

  useEffect(() => {
    if (!jump || !jump.page) return;
    pageRefs.current[jump.page - 1]?.scrollIntoView({ behavior: "smooth", block: jump.box ? "center" : "start" });
  }, [jump]);

  // Selection works with mouse, pen and finger: pointer release, plus a short pause after the
  // selection changes (touch selection handles do not send a pointer release).
  useEffect(() => {
    let timer = 0;
    const onChange = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        const sel = readSelection();
        if (sel && boxRef.current?.contains(window.getSelection()!.anchorNode)) onSelect(sel);
      }, 450);
    };
    document.addEventListener("selectionchange", onChange);
    return () => { document.removeEventListener("selectionchange", onChange); window.clearTimeout(timer); };
  }, [onSelect]);

  const [penNear, setPenNear] = useState(false);
  const nearTimer = useRef(0);
  const notePen = (e: React.PointerEvent) => {
    if (e.pointerType !== "pen") return;
    setPenNear(true);
    window.clearTimeout(nearTimer.current);
    nearTimer.current = window.setTimeout(() => setPenNear(false), 1500);
  };
  const handlePointerUp = (e: React.PointerEvent) => {
    if (pen.on) return;
    const sel = readSelection();
    if (sel) onSelect(sel);
    else if (!(e.target as HTMLElement).closest(".mark, .badge, .ink-mark, .page-pop, .margin-rail")) onSelect(null);
  };
  const handleContext = (e: React.MouseEvent) => {
    const sel = readSelection();
    if (sel) { e.preventDefault(); onContext(sel, e.clientX, e.clientY); }
  };

  if (error) return <div className="error-box">{error}</div>;
  return (
    <div ref={boxRef} dir="ltr" className={`col ${pen.on ? "pen-on" : ""} ${penNear ? "pen-near" : ""}`} style={{ gap: 16, width: "100%" }}
      onPointerUp={handlePointerUp} onContextMenu={handleContext} onPointerMove={notePen} onPointerOver={notePen} onPointerDown={notePen}>
      {pdf && sizes.map((size, i) => {
        const scale = width / size.w;
        return (
          <div key={i} ref={(el) => { pageRefs.current[i] = el; }} data-page={i + 1} className="page-row">
            <div className="page-wrap">
              <PdfPage pdf={pdf} number={i + 1} scale={scale} width={width} height={size.h * scale}
                notes={notes.filter((n) => n.page === i + 1 && n.rects?.length && n.mark !== "ink")} numbers={numbers} activeNote={activeNote}
                inkNotes={notes.filter((n) => n.page === i + 1 && n.mark === "ink" && n.ink?.length)}
                onNoteClick={onNoteClick} flash={jump && jump.page === i + 1 ? jump : null} pen={pen} />
              {renderOverlay?.(i + 1, size.h * scale, width)}
            </div>
            {renderMargin?.(i + 1, size.h * scale)}
          </div>
        );
      })}
    </div>
  );
}

/** Highlight with the pen: the text from where the stroke starts to where it ends, like a text selection. */
function selectAlong(points: number[][], page: HTMLElement): Selection | null {
  page.classList.add("hit-text"); // let the caret search reach the text under existing marks
  try {
    const caretAt = (x: number, y: number) => {
      const r = document.caretRangeFromPoint?.(x, y);
      return r && page.querySelector(".textLayer")?.contains(r.startContainer) && r.startContainer.nodeType === Node.TEXT_NODE ? r : null;
    };
    let first: Range | null = null, last: Range | null = null;
    for (const [x, y] of points) { first = caretAt(x, y); if (first) break; }
    for (let i = points.length - 1; i >= 0; i--) { last = caretAt(points[i][0], points[i][1]); if (last) break; }
    if (!first || !last) return null;
    const range = document.createRange();
    const forward = first.compareBoundaryPoints(Range.START_TO_START, last) <= 0;
    const [a, b] = forward ? [first, last] : [last, first];
    range.setStart(a.startContainer, a.startOffset);
    range.setEnd(b.startContainer, Math.min((b.startContainer.textContent ?? "").length, b.startOffset + 1));
    const sel = window.getSelection()!;
    sel.removeAllRanges(); sel.addRange(range);
    const result = readSelection();
    sel.removeAllRanges();
    return result;
  } finally { page.classList.remove("hit-text"); }
}

function readSelection(): Selection | null {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const text = sel.toString().replace(/\s+/g, " ").trim();
  if (text.length < 1) return null;
  const range = sel.getRangeAt(0);
  const pageEl = (range.startContainer.parentElement as HTMLElement | null)?.closest(".pdf-page") as HTMLElement | null;
  if (!pageEl) return null;
  const box = pageEl.getBoundingClientRect();
  const rects = Array.from(range.getClientRects())
    .filter((r) => r.width > 1 && r.height > 2 && r.left >= box.left - 2 && r.right <= box.right + 2)
    .map((r) => [(r.left - box.left) / box.width, (r.top - box.top) / box.height, r.width / box.width, r.height / box.height]);
  if (!rects.length) return null;
  const all = range.getBoundingClientRect();
  return { page: Number(pageEl.dataset.page), rects, quote: text, segment_id: null, start: null, end: null,
    x: all.left + all.width / 2, y: all.top };
}

type PageProps = {
  pdf: PDFDocumentProxy; number: number; scale: number; width: number; height: number; notes: Note[]; inkNotes: Note[];
  numbers: Record<string, number>; activeNote: string | null; onNoteClick: (id: string) => void; flash: Jump | null;
  pen: Props["pen"];
};

export const strokePath = (pts: number[][], w: number, h: number) =>
  pts.map((p, i) => `${i ? "L" : "M"}${(p[0] * w).toFixed(1)} ${(p[1] * h).toFixed(1)}`).join(" ");

function PdfPage({ pdf, number, scale, width, height, notes, inkNotes, numbers, activeNote, onNoteClick, flash, pen }: PageProps) {
  const [drawing, setDrawing] = useState<InkStroke | null>(null);
  const eraser = useRef<number[][] | null>(null);
  // a pen touching the page with the Pen tool off: draws or highlights once it moves, a tap stays a tap
  const penStroke = useRef<{ id: number; points: number[][]; client: number[][]; started: boolean } | null>(null);
  const [auto, setAuto] = useState<InkStroke | null>(null);
  const isEraser = (e: React.PointerEvent) => e.pointerType === "pen" && (e.button === 5 || (e.buttons & 32) !== 0);
  // remove the ink a path touched; returns false when it touched nothing
  const eraseAlong = (path: number[][], radius: number) => {
    const pendingHits = strokesHit(pen.pending[number] ?? [], path, width, height, radius);
    const saved = inkNotes.map((n) => ({ note: n, strokes: strokesHit(n.ink ?? [], path, width, height, radius) })).filter((x) => x.strokes.length);
    if (!pendingHits.length && !saved.length) return false;
    pen.erase(number, pendingHits, saved);
    return true;
  };
  const pagePoint = (e: React.PointerEvent, el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
  };
  const sawPen = useRef(false);
  const ref = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const io = new IntersectionObserver(([e]) => e.isIntersecting && setVisible(true), { rootMargin: "800px 0px" });
    if (ref.current) io.observe(ref.current);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    let renderTask: { cancel: () => void } | null = null;
    pdf.getPage(number).then(async (page) => {
      if (cancelled || !canvasRef.current || !textRef.current) return;
      const viewport = page.getViewport({ scale });
      const ratio = window.devicePixelRatio || 1;
      const canvas = canvasRef.current;
      canvas.width = Math.floor(viewport.width * ratio);
      canvas.height = Math.floor(viewport.height * ratio);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      const ctx = canvas.getContext("2d")!;
      const task = page.render({ canvasContext: ctx, viewport, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined });
      renderTask = task;
      await task.promise.catch(() => {});
      if (cancelled) return;
      textRef.current.replaceChildren();
      const layer = new pdfjs.TextLayer({ textContentSource: page.streamTextContent(), container: textRef.current, viewport });
      await layer.render().catch(() => {});
    });
    return () => { cancelled = true; renderTask?.cancel(); };
  }, [visible, pdf, number, scale]);

  const style = { width, height, "--scale-factor": scale, "--total-scale-factor": scale, "--user-unit": 1 } as React.CSSProperties;
  return (
    <div ref={ref} className="pdf-page" data-page={number} style={style}
      onPointerDownCapture={(e) => { // the pen's eraser end removes ink, with or without the Pen tool
        if (isEraser(e)) {
        e.preventDefault(); e.stopPropagation();
        try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* pointer already gone */ }
        eraser.current = [pagePoint(e, e.currentTarget)];
        return;
      }
        if (e.pointerType !== "pen" || pen.on || pen.mode === "select" || e.button !== 0) return;
        if ((e.target as HTMLElement).closest(".sticky, .sticky-icon, .page-pop, .badge, button, textarea, input")) return;
        e.preventDefault(); // no text selection: the pen draws or highlights instead
        penStroke.current = { id: e.pointerId, points: [pagePoint(e, e.currentTarget)], client: [[e.clientX, e.clientY]], started: false };
      }}
      onPointerMoveCapture={(e) => {
        if (eraser.current) { e.stopPropagation(); eraser.current.push(pagePoint(e, e.currentTarget)); return; }
        const st = penStroke.current;
        if (!st || st.id !== e.pointerId) return;
        st.points.push(pagePoint(e, e.currentTarget).map((v) => Math.round(v * 10000) / 10000));
        st.client.push([e.clientX, e.clientY]);
        if (!st.started && Math.hypot(e.clientX - st.client[0][0], e.clientY - st.client[0][1]) > 4) {
          st.started = true;
          try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* pointer already gone */ }
        }
        if (st.started) {
          e.stopPropagation();
          setAuto(pen.mode === "highlight" ? { color: colorOf(pen.highlightColor), width: 14, points: [...st.points] }
            : { color: pen.color, width: pen.width, points: [...st.points] });
        }
      }}
      onPointerUpCapture={(e) => {
        if (eraser.current) { e.stopPropagation(); eraseAlong(eraser.current, 10); eraser.current = null; return; }
        const st = penStroke.current;
        if (!st || st.id !== e.pointerId) return;
        penStroke.current = null;
        setAuto(null);
        if (!st.started) return;
        e.stopPropagation();
        if (pen.mode === "highlight") {
          const s = selectAlong(st.client, e.currentTarget);
          if (s) pen.onHighlight(s);
        } else if (!(isScratch(st.points, width, height) && eraseAlong(st.points, 6))) {
          pen.add(number, { color: pen.color, width: pen.width, points: st.points });
        }
      }}
      onPointerCancelCapture={() => { penStroke.current = null; eraser.current = null; setAuto(null); }}>
      <canvas ref={canvasRef} />
      <div ref={textRef} className="textLayer" />
      {notes.map((n) => n.rects.map((r, j) => (
        <div key={`${n.id}-${j}`} className={`mark ${n.mark === "underline" ? "underline" : ""} ${n.mark === "redact" ? "redact" : ""} ${activeNote === n.id ? "active" : ""}`}
          onClick={() => onNoteClick(n.id)} title={n.mark === "redact" ? "Redaction" : n.body || n.quote}
          style={{ left: `${r[0] * 100}%`, top: `${r[1] * 100}%`, width: `${r[2] * 100}%`, height: `${r[3] * 100}%`,
            ...(n.mark === "redact" ? {} : { background: colorOf(n.color), borderColor: colorOf(n.color) === "#FFC999" ? "#F08C2E" : colorOf(n.color) }) }} />
      )))}
      {notes.filter((n) => n.mark !== "redact").map((n) => (
        <button key={`b-${n.id}`} className="badge" aria-label={`Note ${numbers[n.id]}`} onClick={() => onNoteClick(n.id)}
          style={{ right: -34, top: `calc(${n.rects[0][1] * 100}% - 2px)`, background: colorOf(n.color) }}>{numbers[n.id]}</button>
      ))}
      <svg className="ink-layer" width={width} height={height} aria-hidden="true">
        {inkNotes.map((n) => (
          <g key={n.id} className="ink-mark" onClick={() => onNoteClick(n.id)}
            style={{ opacity: activeNote && activeNote !== n.id ? 0.75 : 1, cursor: "pointer", pointerEvents: pen.on ? "none" : "stroke" }}>
            {n.ink!.map((st, j) => <path key={j} d={strokePath(st.points, width, height)} stroke={st.color} strokeWidth={st.width * scale}
              fill="none" strokeLinecap="round" strokeLinejoin="round" />)}
          </g>
        ))}
        {auto && pen.mode === "highlight" && <path d={strokePath(auto.points, width, height)} stroke={auto.color} strokeWidth={auto.width}
          opacity={0.45} fill="none" strokeLinecap="round" strokeLinejoin="round" />}
        {[...(pen.pending[number] ?? []), ...(drawing ? [drawing] : []), ...(auto && pen.mode !== "highlight" ? [auto] : [])].map((st, j) => (
          <path key={`p${j}`} d={strokePath(st.points, width, height)} stroke={st.color} strokeWidth={st.width * scale} fill="none"
            strokeLinecap="round" strokeLinejoin="round" />
        ))}
      </svg>
      {inkNotes.map((n) => {
        const ys = n.ink!.flatMap((st) => st.points.map((p) => p[1]));
        return <button key={`ib-${n.id}`} className="badge" aria-label={`Note ${numbers[n.id]}`} onClick={() => onNoteClick(n.id)}
          style={{ right: -34, top: `calc(${Math.min(...ys) * 100}% - 2px)`, background: "#FDECEA" }}>{numbers[n.id]}</button>;
      })}
      {pen.on && (
        <div className="ink-input"
          onPointerDown={(e) => {
            if (e.pointerType === "pen") sawPen.current = true;
            if (e.pointerType === "touch" && sawPen.current) return; // palm rejection while using a pen
            e.currentTarget.setPointerCapture(e.pointerId);
            const r = e.currentTarget.getBoundingClientRect();
            setDrawing({ color: pen.color, width: pen.width, points: [[(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]] });
          }}
          onPointerMove={(e) => {
            if (!drawing || !e.currentTarget.hasPointerCapture(e.pointerId)) return;
            const r = e.currentTarget.getBoundingClientRect();
            const pt = [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
            const last = drawing.points[drawing.points.length - 1];
            if (Math.hypot((pt[0] - last[0]) * r.width, (pt[1] - last[1]) * r.height) >= 1.5)
              setDrawing({ ...drawing, points: [...drawing.points, pt.map((v) => Math.round(v * 10000) / 10000)] });
          }}
          onPointerUp={() => {
            // a scratch-out over ink erases it; anywhere else the scribble stays as ink
            if (drawing && drawing.points.length > 1 && !(isScratch(drawing.points, width, height) && eraseAlong(drawing.points, 6)))
              pen.add(number, drawing);
            setDrawing(null);
          }}
          onPointerCancel={() => setDrawing(null)} />
      )}
      {flash?.box && <div key={flash.ts} className="flash" style={{ left: `${flash.box[0] * 100 - 0.5}%`, top: `${flash.box[1] * 100 - 0.5}%`,
        width: `${(flash.box[2] - flash.box[0]) * 100 + 1}%`, height: `${(flash.box[3] - flash.box[1]) * 100 + 1}%` }} />}
    </div>
  );
}
