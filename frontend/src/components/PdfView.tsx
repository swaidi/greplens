import { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { Note } from "../api";
import type { Jump, Selection } from "./Workspace";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
const COLORS: Record<string, string> = { yellow: "#FFE58A", green: "#BDEBC8", blue: "#BFDDFB", pink: "#F9C9DD", orange: "#FFC999" };
export const colorOf = (c: string | null) => COLORS[c ?? "yellow"] ?? COLORS.yellow;

type Props = {
  url: string; notes: Note[]; numbers: Record<string, number>; activeNote: string | null; jump: Jump | null;
  onSelect: (s: Selection | null) => void; onNoteClick: (id: string) => void; onPage: (n: number, total: number) => void;
  onContext: (s: Selection, x: number, y: number) => void;
};

export default function PdfView({ url, notes, numbers, activeNote, jump, onSelect, onNoteClick, onPage, onContext }: Props) {
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
    const ro = new ResizeObserver(() => setWidth(Math.min(1000, Math.max(320, el.clientWidth - 64))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

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

  const handleMouseUp = (e: React.MouseEvent) => {
    const sel = readSelection();
    if (sel) onSelect({ ...sel, x: e.clientX, y: e.clientY });
    else if (!(e.target as HTMLElement).closest(".mark, .badge")) onSelect(null);
  };
  const handleContext = (e: React.MouseEvent) => {
    const sel = readSelection();
    if (sel) { e.preventDefault(); onContext({ ...sel, x: e.clientX, y: e.clientY }, e.clientX, e.clientY); }
  };

  if (error) return <div className="error-box">{error}</div>;
  return (
    <div ref={boxRef} className="col" style={{ gap: 16, width: "100%" }} onMouseUp={handleMouseUp} onContextMenu={handleContext}>
      {pdf && sizes.map((size, i) => {
        const scale = width / size.w;
        return (
          <div key={i} ref={(el) => { pageRefs.current[i] = el; }} data-page={i + 1}>
            <PdfPage pdf={pdf} number={i + 1} scale={scale} width={width} height={size.h * scale}
              notes={notes.filter((n) => n.page === i + 1 && n.rects?.length)} numbers={numbers} activeNote={activeNote}
              onNoteClick={onNoteClick} flash={jump && jump.page === i + 1 ? jump : null} />
          </div>
        );
      })}
    </div>
  );
}

function readSelection(): Omit<Selection, "x" | "y"> | null {
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
  return { page: Number(pageEl.dataset.page), rects, quote: text, segment_id: null, start: null, end: null };
}

type PageProps = {
  pdf: PDFDocumentProxy; number: number; scale: number; width: number; height: number; notes: Note[];
  numbers: Record<string, number>; activeNote: string | null; onNoteClick: (id: string) => void; flash: Jump | null;
};

function PdfPage({ pdf, number, scale, width, height, notes, numbers, activeNote, onNoteClick, flash }: PageProps) {
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
    <div ref={ref} className="pdf-page" data-page={number} style={style}>
      <canvas ref={canvasRef} />
      <div ref={textRef} className="textLayer" />
      {notes.map((n) => n.rects.map((r, j) => (
        <div key={`${n.id}-${j}`} className={`mark ${n.mark === "underline" ? "underline" : ""} ${activeNote === n.id ? "active" : ""}`}
          onClick={() => onNoteClick(n.id)} title={n.body || n.quote}
          style={{ left: `${r[0] * 100}%`, top: `${r[1] * 100}%`, width: `${r[2] * 100}%`, height: `${r[3] * 100}%`,
            background: colorOf(n.color), borderColor: colorOf(n.color) === "#FFC999" ? "#F08C2E" : colorOf(n.color) }} />
      )))}
      {notes.map((n) => (
        <button key={`b-${n.id}`} className="badge" aria-label={`Note ${numbers[n.id]}`} onClick={() => onNoteClick(n.id)}
          style={{ right: -34, top: `calc(${n.rects[0][1] * 100}% - 2px)`, background: colorOf(n.color) }}>{numbers[n.id]}</button>
      ))}
      {flash?.box && <div key={flash.ts} className="flash" style={{ left: `${flash.box[0] * 100 - 0.5}%`, top: `${flash.box[1] * 100 - 0.5}%`,
        width: `${(flash.box[2] - flash.box[0]) * 100 + 1}%`, height: `${(flash.box[3] - flash.box[1]) * 100 + 1}%` }} />}
    </div>
  );
}
