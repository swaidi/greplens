import { useEffect, useRef } from "react";
import type { Note, Segment } from "../api";
import type { Jump, Selection } from "./Workspace";
import { colorOf } from "./PdfView";

type Props = {
  segments: Segment[]; notes: Note[]; numbers: Record<string, number>; activeNote: string | null; jump: Jump | null;
  onSelect: (s: Selection | null) => void; onNoteClick: (id: string) => void; onContext: (s: Selection, x: number, y: number) => void;
  renderMargin?: (segmentId: string) => React.ReactNode;
  renderOverlay?: (segmentId: string) => React.ReactNode;
};

/** Word, Excel, PowerPoint and text documents: shown as paragraphs, with marks stored as character offsets. */
export default function TextView({ segments, notes, numbers, activeNote, jump, onSelect, onNoteClick, onContext, renderMargin, renderOverlay }: Props) {
  const refs = useRef<Record<string, HTMLParagraphElement | null>>({});

  useEffect(() => {
    if (!jump?.segment_id) return;
    const el = refs.current[jump.segment_id];
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.remove("flash-p");
    void el.offsetWidth;
    el.classList.add("flash-p");
  }, [jump]);

  const articleRef = useRef<HTMLElement>(null);
  const read = (): Selection | null => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    const range = sel.getRangeAt(0);
    const p = (range.startContainer.parentElement as HTMLElement | null)?.closest("p[data-seg]") as HTMLElement | null;
    const endP = (range.endContainer.parentElement as HTMLElement | null)?.closest("p[data-seg]");
    if (!p || p !== endP) return null; // marks stay inside one paragraph
    const pre = document.createRange();
    pre.setStart(p, 0);
    pre.setEnd(range.startContainer, range.startOffset);
    const start = pre.toString().length;
    const quote = range.toString();
    if (!quote.trim()) return null;
    const seg = segments.find((s) => s.id === p.dataset.seg)!;
    const box = range.getBoundingClientRect();
    return { page: seg.page, rects: [], quote: quote.trim(), segment_id: seg.id, start, end: start + quote.length,
      x: box.left + box.width / 2, y: box.top };
  };
  const readRef = useRef(read);
  readRef.current = read;
  useEffect(() => {  // touch selection handles send no pointer release: react to a pause in selection changes
    let timer = 0;
    const onChange = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        const s = readRef.current();
        if (s && articleRef.current?.contains(window.getSelection()!.anchorNode)) onSelect(s);
      }, 450);
    };
    document.addEventListener("selectionchange", onChange);
    return () => { document.removeEventListener("selectionchange", onChange); window.clearTimeout(timer); };
  }, [onSelect]);

  let lastHeader = "";
  return (
    <article ref={articleRef} className={`text-doc ${renderMargin ? "with-margin" : ""}`} dir="auto"
      onPointerUp={(e) => { const s = read(); if (s) onSelect(s); else if (!(e.target as HTMLElement).closest("mark, .page-pop, .margin-rail")) onSelect(null); }}
      onContextMenu={(e) => { const s = read(); if (s) { e.preventDefault(); onContext(s, e.clientX, e.clientY); } }}>
      {segments.map((seg) => {
        const header = [seg.article, seg.approx_page ? `≈ Page ${seg.page}` : seg.page ? `Page ${seg.page}` : ""].filter(Boolean).join(" · ");
        const showHeader = header !== lastHeader;
        lastHeader = header;
        const marks = notes.filter((n) => n.segment_id === seg.id && n.start_offset != null && n.end_offset != null)
          .sort((a, b) => a.start_offset! - b.start_offset!);
        const parts: React.ReactNode[] = [];
        let cursor = 0;
        for (const n of marks) {
          if (n.start_offset! < cursor) continue;
          parts.push(seg.text.slice(cursor, n.start_offset!));
          parts.push(
            <mark key={n.id} data-n={numbers[n.id]} className={n.mark === "underline" ? "underline" : ""} onClick={() => onNoteClick(n.id)} title={n.body}
              style={{ background: colorOf(n.color), textDecorationColor: n.color === "orange" ? "#F08C2E" : colorOf(n.color),
                outline: activeNote === n.id ? "2px solid var(--accent)" : undefined }}>
              {seg.text.slice(n.start_offset!, n.end_offset!)}
            </mark>);
          cursor = n.end_offset!;
        }
        parts.push(seg.text.slice(cursor));
        return (
          <div key={seg.id} className={renderMargin ? "seg-row" : undefined}>
            <div>
              {showHeader && header && <div className="loc">{header}</div>}
              <p data-seg={seg.id} ref={(el) => { refs.current[seg.id] = el; }}>{parts}</p>
              {renderOverlay?.(seg.id)}
            </div>
            {renderMargin?.(seg.id)}
          </div>
        );
      })}
    </article>
  );
}
