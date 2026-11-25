import { useEffect, useState } from "react";
import { api, ProviderInfo, Workspace } from "../api";
import { useI18n } from "../i18n";

/** Which AI answers in this workspace: Local (default, offline) or a cloud provider with a saved key. */
export default function AiPicker({ ws, onChange }: { ws: Workspace; onChange: (w: Workspace) => void }) {
  const { t } = useI18n();
  const [list, setList] = useState<ProviderInfo[]>([]);
  const [confirm, setConfirm] = useState<ProviderInfo | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api.get<ProviderInfo[]>("/api/providers").then(setList).catch(() => {}); }, []);
  const current = ws.provider || "local";
  const usable = list.filter((p) => p.has_key || p.id === current);
  const set = async (provider: string) => {
    setError("");
    try { onChange(await api.patch<Workspace>(`/api/workspaces/${ws.id}`, { provider })); }
    catch (e) { setError(String(e).replace(/^Error: /, "")); }
  };
  return (
    <>
      <label className="row" style={{ gap: 6 }} title={t("aiForWorkspace")}>
        <span className="muted small">{t("aiShort")}</span>
        <select className="input" style={{ minHeight: 34, width: "auto", paddingBlock: 4 }} value={current}
          onChange={(e) => {
            const p = list.find((x) => x.id === e.target.value);
            if (p) setConfirm(p); else set("local");
          }}>
          <option value="local">{t("localAi")}</option>
          {usable.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        {current !== "local" && <span className="pill ai">{t("cloudOn")}</span>}
      </label>
      {error && <span className="error-box small">{error}</span>}
      {confirm && (
        <div className="modal-back" onClick={() => setConfirm(null)}>
          <div className="modal" role="dialog" aria-label={t("useCloudTitle", { n: confirm.name })} onClick={(e) => e.stopPropagation()}>
            <h2 style={{ fontSize: 18 }}>{t("useCloudTitle", { n: confirm.name })}</h2>
            <p style={{ margin: 0 }}>{t("useCloudBody", { n: confirm.name })}</p>
            <p className="muted small" style={{ margin: 0 }}>{t("useCloudPolicy")}</p>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button className="btn" onClick={() => setConfirm(null)}>{t("cancel")}</button>
              <button className="btn primary" onClick={() => { set(confirm.id); setConfirm(null); }}>{t("useCloudOk")}</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
