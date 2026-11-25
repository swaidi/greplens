import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useI18n } from "../i18n";
import { IconClose, IconTrash } from "../icons";

type Instruction = { id: string; name: string; enabled: number; chars: number; text: string };

/** Rules and reference files attached to a workspace; used by Ask, Review and Create. */
export default function InstructionsDialog({ workspaceId, onClose }: { workspaceId: string; onClose: () => void }) {
  const { t } = useI18n();
  const [list, setList] = useState<Instruction[]>([]);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const load = () => api.get<Instruction[]>(`/api/workspaces/${workspaceId}/instructions`).then(setList);
  useEffect(() => { load(); }, [workspaceId]); // eslint-disable-line
  const upload = async (f: File) => {
    setError("");
    try { await api.upload(`/api/workspaces/${workspaceId}/instructions`, f); load(); }
    catch (e) { setError(String(e).replace(/^Error: /, "")); }
  };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 620 }} role="dialog" aria-label={t("instructionsTitle")} onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ fontSize: 18 }}>{t("instructionsTitle")}</h2>
          <button className="icon-btn" aria-label={t("close")} onClick={onClose}><IconClose /></button>
        </div>
        <p className="muted small" style={{ margin: 0 }}>{t("instructionsHint")}</p>
        <input ref={fileRef} type="file" hidden accept=".txt,.md,.docx,.pdf"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
        <div><button className="btn primary" onClick={() => fileRef.current?.click()}>+ {t("addInstructionFile")}</button></div>
        {error && <div className="error-box small">{error}</div>}
        {!list.length && <span className="muted small">{t("noInstructions")}</span>}
        <div className="col" style={{ maxHeight: 380, overflow: "auto" }}>
          {list.map((f) => (
            <div key={f.id} className="card col" style={{ padding: 10, gap: 6 }}>
              <div className="row wrap">
                <strong className="grow" dir="auto">{f.name}</strong>
                <span className="muted small">{t("chars", { n: f.chars.toLocaleString() })}</span>
                <button className="btn small" onClick={() => setOpen(open === f.id ? null : f.id)}>{open === f.id ? t("hide") : t("view")}</button>
                <button className={`toggle ${f.enabled ? "on" : ""}`} aria-pressed={!!f.enabled} aria-label={t("useInstruction")}
                  onClick={async () => { await api.patch(`/api/instructions/${f.id}`, { enabled: !f.enabled }); load(); }} />
                <button className="icon-btn" aria-label={t("delete")} onClick={async () => { await api.del(`/api/instructions/${f.id}`); load(); }}><IconTrash /></button>
              </div>
              {open === f.id && <pre className="small" dir="auto" style={{ whiteSpace: "pre-wrap", maxHeight: 200, overflow: "auto", margin: 0,
                background: "var(--soft2)", padding: 8, borderRadius: 6 }}>{f.text.slice(0, 6000)}{f.text.length > 6000 ? "\n…" : ""}</pre>}
            </div>
          ))}
        </div>
        <span className="muted small">{t("instructionsLocalNote")}</span>
      </div>
    </div>
  );
}
