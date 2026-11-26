import { useRef, useState } from "react";
import type { InkStroke } from "../api";
import { useI18n } from "../i18n";

/** Handwritten notes: strokes stored with x from 0 to 1 and y in the same units; Windows reads them as text for search. */
const PEN_COLORS = ["#17202B", "#1D4F91", "#D92D20"];
const path = (pts: number[][], k: number) => pts.map((p, i) => `${i ? "L" : "M"}${(p[0] * k).toFixed(1)} ${(p[1] * k).toFixed(1)}`).join(" ");

export function HandwritingView({ strokes }: { strokes: InkStroke[] }) {
  const { t } = useI18n();
  const maxY = Math.max(0.12, ...strokes.flatMap((s) => s.points.map((p) => p[1]))) + 0.03;
  return (
    <svg className="hw-paper" viewBox={`0 0 600 ${maxY * 600}`} width="100%" role="img" aria-label={t("handwriting")}>
      {strokes.map((s, i) => <path key={i} d={path(s.points, 600)} stroke={s.color} strokeWidth={s.width * 1.2} fill="none"
        strokeLinecap="round" strokeLinejoin="round" />)}
    </svg>
  );
}

export function HandwritingPad({ initial, initialLang, onSave, onCancel }: { initial: InkStroke[]; initialLang?: string;
  onSave: (s: InkStroke[], lang: string) => void; onCancel: () => void }) {
  const { t, lang: uiLang } = useI18n();
  const [lang, setLang] = useState(initialLang || uiLang);
  const [strokes, setStrokes] = useState<InkStroke[]>(initial);
  const [current, setCurrent] = useState<InkStroke | null>(null);
  const [color, setColor] = useState(PEN_COLORS[0]);
  const box = useRef<HTMLDivElement>(null);
  const sawPen = useRef(false);
  const point = (e: React.PointerEvent) => {
    const r = box.current!.getBoundingClientRect();
    return [Math.round(((e.clientX - r.left) / r.width) * 10000) / 10000, Math.round(((e.clientY - r.top) / r.width) * 10000) / 10000];
  };
  const width = box.current?.clientWidth ?? 300;

  return (
    <div className="col" style={{ gap: 6 }} onClick={(e) => e.stopPropagation()}>
      <div ref={box} className="hw-paper hw-pad"
        onPointerDown={(e) => {
          if (e.pointerType === "pen") sawPen.current = true;
          if (e.pointerType === "touch" && sawPen.current) return; // ignore the palm while writing with a pen
          e.currentTarget.setPointerCapture(e.pointerId);
          setCurrent({ color, width: 2.5, points: [point(e)] });
        }}
        onPointerMove={(e) => {
          if (!current || !e.currentTarget.hasPointerCapture(e.pointerId)) return;
          setCurrent({ ...current, points: [...current.points, point(e)] });
        }}
        onPointerUp={() => { if (current && current.points.length > 1) setStrokes((s) => [...s, current]); setCurrent(null); }}
        onPointerCancel={() => setCurrent(null)}>
        <svg width="100%" height="100%" aria-hidden="true">
          {[...strokes, ...(current ? [current] : [])].map((s, i) => (
            <path key={i} d={path(s.points, width)} stroke={s.color} strokeWidth={s.width} fill="none" strokeLinecap="round" strokeLinejoin="round" />
          ))}
        </svg>
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        {PEN_COLORS.map((c) => (
          <button key={c} className={`pen-dot ${color === c ? "on" : ""}`} style={{ background: c, width: 26, height: 26 }}
            aria-label={c} aria-pressed={color === c} onClick={() => setColor(c)} />
        ))}
        <button className="btn small" disabled={!strokes.length} onClick={() => setStrokes((s) => s.slice(0, -1))}>{t("penUndo")}</button>
        <button className="btn small" disabled={!strokes.length} onClick={() => setStrokes([])}>{t("hwClear")}</button>
        <span className="grow" />
        <div className="seg" role="group" aria-label={t("hwLang")} title={t("hwLang")}>
          <button className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>EN</button>
          <button className={lang === "ar" ? "on" : ""} onClick={() => setLang("ar")}>عربي</button>
        </div>
        <button className="btn small" onClick={onCancel}>{t("cancel")}</button>
        <button className="btn small primary" onClick={() => onSave(strokes, lang)}>{t("hwSave")}</button>
      </div>
      <span className="muted small">{t("hwHint")}</span>
    </div>
  );
}
