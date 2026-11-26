import { useEffect } from "react";
import type { InkStroke } from "./api";

/**
 * A scratch-out: a quick back-and-forth scribble, like crossing something out on paper.
 * Points are page fractions; w and h turn them into screen pixels.
 */
export function isScratch(points: number[][], w: number, h: number): boolean {
  if (points.length < 8) return false;
  const px = points.map(([x, y]) => [x * w, y * h]);
  const xs = px.map((p) => p[0]), ys = px.map((p) => p[1]);
  const bw = Math.max(...xs) - Math.min(...xs), bh = Math.max(...ys) - Math.min(...ys);
  if (Math.max(bw, bh) < 14) return false;
  const horizontal = bw >= bh; // reversals are counted along the longer side
  let turns = 0, dir = 0, run = 0, length = 0;
  for (let i = 1; i < px.length; i++) {
    const d = horizontal ? px[i][0] - px[i - 1][0] : px[i][1] - px[i - 1][1];
    length += Math.hypot(px[i][0] - px[i - 1][0], px[i][1] - px[i - 1][1]);
    if (Math.abs(d) < 0.5) continue;
    const s = Math.sign(d);
    if (dir && s !== dir && run > 6) { turns++; run = 0; }
    if (s !== dir) run = 0;
    run += Math.abs(d);
    dir = s;
  }
  return turns >= 4 && length > 2.5 * Math.max(bw, bh);
}

/** Which strokes a scratch-out or eraser touches: any stroke with a point within `radius` pixels of the path. */
export function strokesHit(strokes: InkStroke[], path: number[][], w: number, h: number, radius = 8): number[] {
  const xs = path.map((p) => p[0] * w), ys = path.map((p) => p[1] * h);
  const box = [Math.min(...xs) - radius, Math.min(...ys) - radius, Math.max(...xs) + radius, Math.max(...ys) + radius];
  const hit: number[] = [];
  strokes.forEach((s, i) => {
    const near = s.points.some(([x, y]) => {
      const X = x * w, Y = y * h;
      if (X < box[0] || X > box[2] || Y < box[1] || Y > box[3]) return false;
      return path.length < 3 || xs.some((px, k) => Math.hypot(px - X, ys[k] - Y) <= radius * 2.5);
    });
    if (near) hit.push(i);
  });
  return hit;
}

/** Two-finger tap = undo, three-finger tap = redo, on touch screens. */
export function useTapGestures(target: () => HTMLElement | null, onUndo: () => void, onRedo: () => void) {
  useEffect(() => {
    const el = target();
    if (!el) return;
    let start = 0, most = 0, moved = false;
    const origin = new Map<number, [number, number]>();
    const down = (e: TouchEvent) => {
      if (!origin.size) { start = Date.now(); most = 0; moved = false; }
      for (const t of Array.from(e.changedTouches)) origin.set(t.identifier, [t.clientX, t.clientY]);
      most = Math.max(most, e.touches.length);
    };
    const move = (e: TouchEvent) => {
      for (const t of Array.from(e.changedTouches)) {
        const o = origin.get(t.identifier);
        if (o && Math.hypot(t.clientX - o[0], t.clientY - o[1]) > 14) moved = true;
      }
    };
    const up = (e: TouchEvent) => {
      for (const t of Array.from(e.changedTouches)) origin.delete(t.identifier);
      if (e.touches.length) return;
      origin.clear();
      if (moved || Date.now() - start > 400) return;
      if (most === 2) onUndo();
      else if (most === 3) onRedo();
    };
    el.addEventListener("touchstart", down, { passive: true });
    el.addEventListener("touchmove", move, { passive: true });
    el.addEventListener("touchend", up, { passive: true });
    el.addEventListener("touchcancel", up, { passive: true });
    return () => {
      el.removeEventListener("touchstart", down); el.removeEventListener("touchmove", move);
      el.removeEventListener("touchend", up); el.removeEventListener("touchcancel", up);
    };
  }, [target, onUndo, onRedo]);
}
