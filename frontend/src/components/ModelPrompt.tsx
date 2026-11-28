import { useEffect, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";

type Item = { status: string; done_bytes: number; total: number; message: string };

/** A document waiting for the search model: one button downloads it, then indexing starts by itself. */
export function NeedsSearchModel({ onReady }: { onReady: () => void }) {
  const { t } = useI18n();
  const [item, setItem] = useState<Item | null>(null);
  const [error, setError] = useState("");
  const active = !!item && ["queued", "downloading", "verifying"].includes(item.status);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(async () => {
      const p = await api.get<{ items: Record<string, Item> }>("/api/models/progress");
      const next = p.items["bge-m3"] ?? null;
      setItem(next);
      if (next?.status === "done") onReady();
    }, 1000);
    return () => clearInterval(timer);
  }, [active, onReady]);
  const pct = item?.total ? Math.floor((item.done_bytes / item.total) * 100) : 0;
  return (
    <div className="card col" style={{ width: "100%", maxWidth: 560, marginTop: 40, gap: 10 }}>
      <strong>{t("searchModelNeeded")}</strong>
      <span className="muted">{t("searchModelNeededHint")}</span>
      {item?.status === "done" ? <span style={{ color: "var(--ok-text)" }}>{t("searchModelReady")}</span>
        : active ? (
          <>
            <div className="bar"><div style={{ width: `${Math.max(2, pct)}%` }} /></div>
            <span className="muted small">{pct}% · {item?.message}</span>
          </>
        ) : (
          <div><button className="btn primary big" onClick={async () => {
            setError("");
            try {
              const p = await api.post<{ items: Record<string, Item> }>("/api/models/download-search-model");
              setItem(p.items["bge-m3"] ?? { status: "queued", done_bytes: 0, total: 0, message: "" });
            } catch (e) { setError(String(e).replace(/^Error: /, "")); }
          }}>{t("downloadIt")}</button></div>
        )}
      {item?.status === "error" && <div className="error-box small">{item.message}</div>}
      {error && <div className="error-box small">{error}</div>}
      <span className="muted small">{t("searchModelHaveFile")}</span>
    </div>
  );
}
