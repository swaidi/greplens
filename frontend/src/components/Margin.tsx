import { useLayoutEffect, useRef, useState } from "react";

type Item = { id: string; y: number };

/** Notes beside a page, each level with its highlight; when they would overlap they are pushed down. */
export function MarginRail({ items, height, render }: { items: Item[]; height: number; render: (id: string) => React.ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const [tops, setTops] = useState<Record<string, number>>({});
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const place = () => {
      const next: Record<string, number> = {};
      let last = 0;
      for (const it of [...items].sort((a, b) => a.y - b.y)) {
        const card = el.querySelector<HTMLElement>(`[data-rail="${it.id}"]`);
        const top = Math.max(it.y - 6, last);
        next[it.id] = Math.round(top);
        last = top + (card?.offsetHeight ?? 64) + 8;
      }
      setTops((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
    };
    place();
    const ro = new ResizeObserver(place);
    el.querySelectorAll("[data-rail]").forEach((c) => ro.observe(c));
    return () => ro.disconnect();
  });
  return (
    <div ref={box} className="margin-rail" style={{ height }}>
      {items.map((it) => (
        <div key={it.id} data-rail={it.id} className="rail-item" style={{ top: tops[it.id] ?? it.y }}>{render(it.id)}</div>
      ))}
    </div>
  );
}
