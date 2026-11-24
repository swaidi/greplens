import { useEffect, useState } from "react";
import { api, Profile } from "../api";
import { Key, useI18n } from "../i18n";
import { IconBack, IconTrash } from "../icons";
import type { View } from "../App";

type Hardware = { cpu: string; cores: number; ram_gb: number; dedicated_gpu: string | null; gpus: string[]; measured: Record<string, number> };
type ModelsInfo = { installed: Record<string, { name: string; path: string; source: string }>;
  updates: { role: string; new: string; size_gb: number; id: string }[]; engine: Record<string, boolean> };
type Settings = { language: string; update_check: boolean; author: string; models_dir: string; engine: string };
type Term = { id: string; en: string; ar: string };
const SECTIONS: Key[] = ["deviceModels", "language", "reviewCategories", "glossary", "updates", "about"];
const ROLE: Record<string, Key> = { fast: "feat_qa", review: "feat_review", embed: "feat_search" };

export default function SettingsView({ go, back }: { go: (v: View) => void; back: View }) {
  const { t, lang, setLang } = useI18n();
  const [section, setSection] = useState<Key>("deviceModels");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [hw, setHw] = useState<Hardware | null>(null);
  const [models, setModels] = useState<ModelsInfo | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [terms, setTerms] = useState<Term[]>([]);
  const [editing, setEditing] = useState<Partial<Profile> | null>(null);
  const [term, setTerm] = useState({ en: "", ar: "" });
  const [updateMsg, setUpdateMsg] = useState("");

  const load = () => {
    api.get<Settings>("/api/settings").then(setSettings);
    api.get<Hardware>("/api/hardware").then(setHw);
    api.get<ModelsInfo>("/api/models").then(setModels);
    api.get<Profile[]>("/api/profiles").then(setProfiles);
    api.get<Term[]>("/api/glossary").then(setTerms);
  };
  useEffect(load, []);
  const patch = async (body: Partial<Settings>) => setSettings(await api.patch<Settings>("/api/settings", body));

  return (
    <div>
      <header className="topbar">
        <button className="btn" onClick={() => go(back)}><IconBack />{t("back")}</button>
        <h1 style={{ fontSize: 18 }}>{t("settings")}</h1>
      </header>
      <div className="page" style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "flex-start", gap: 24 }}>
        <nav className="settings-nav" aria-label={t("settings")}>
          {SECTIONS.map((s) => <button key={s} className={section === s ? "on" : ""} onClick={() => setSection(s)}>{t(s)}</button>)}
        </nav>
        <section className="card col" style={{ flex: "999 1 560px", minWidth: 0, padding: "24px 26px", gap: 16 }}>
          <h2 style={{ fontSize: 20 }}>{t(section)}</h2>

          {section === "deviceModels" && hw && models && (
            <>
              <div className="grid3">
                <div className="card"><div className="muted small">{t("processor")}</div><strong>{hw.cpu}</strong></div>
                <div className="card"><div className="muted small">{t("memory")}</div><strong>{hw.ram_gb} GB</strong></div>
                <div className="card"><div className="muted small">{t("graphics")}</div><strong>{hw.dedicated_gpu ?? t("noneDetected")}</strong></div>
              </div>
              {(["fast", "review", "embed"] as const).map((role) => {
                const m = models.installed[role];
                return (
                  <div key={role} className="card row wrap" style={{ justifyContent: "space-between" }}>
                    <span className="col" style={{ gap: 0 }}><strong>{m?.name ?? "—"}</strong>
                      <span className="muted small">{t(ROLE[role])} {m ? `· ${m.source}` : ""}</span>
                      {m && <span className="muted small" dir="ltr">{m.path}</span>}</span>
                    <span className="small">{hw.measured[role] ? `${t("measured")}: ${hw.measured[role]} ${t("wordsPerSec")}` : ""}</span>
                    <span className={`pill ${m ? "ok" : "warn"}`}>{m ? t("installed") : t("notInstalled")}</span>
                  </div>
                );
              })}
              {models.updates.map((u) => <div key={u.role} className="banner" style={{ borderRadius: 10 }}>{t("modelUpdate", { n: u.new, g: u.size_gb })}</div>)}
              <div className="row wrap"><button className="btn primary" onClick={async () => { await api.patch("/api/settings", { setup_complete: false }); window.location.reload(); }}>{t("runSetup")}</button></div>
            </>
          )}

          {section === "language" && (
            <div className="col">
              {(["en", "ar"] as const).map((l) => (
                <button key={l} className={`mode-card ${lang === l ? "on" : ""}`} onClick={() => setLang(l)}>
                  <span className="radio" /><span className="col" style={{ gap: 0 }}><strong>{l === "en" ? "English" : "العربية · Arabic"}</strong>
                    <span className="muted small">{l === "en" ? "Default" : "Full right-to-left layout"}</span></span>
                </button>
              ))}
            </div>
          )}

          {section === "reviewCategories" && (
            <>
              <p className="muted" style={{ margin: 0 }}>{t("categoriesHint")}</p>
              <div><button className="btn primary" onClick={() => setEditing({ name: "", name_ar: "", focus: [] })}>+ {t("newCategory")}</button></div>
              {editing && (
                <div className="card col" style={{ background: "#F6F8FA" }}>
                  <div className="grid2">
                    <label className="col"><span>{t("name")} (EN)</span><input className="input" value={editing.name ?? ""} onChange={(e) => setEditing({ ...editing, name: e.target.value })} /></label>
                    <label className="col"><span>{t("name")} (AR)</span><input className="input" dir="rtl" value={editing.name_ar ?? ""} onChange={(e) => setEditing({ ...editing, name_ar: e.target.value })} /></label>
                  </div>
                  <label className="col"><span>{t("focusAreas")}</span>
                    <textarea className="input" rows={6} dir="auto" value={(editing.focus ?? []).join("\n")}
                      onChange={(e) => setEditing({ ...editing, focus: e.target.value.split("\n") })} /></label>
                  <div className="row"><button className="btn" onClick={() => setEditing(null)}>{t("cancel")}</button>
                    <button className="btn primary" disabled={!editing.name?.trim()} onClick={async () => {
                      await api.post("/api/profiles", editing); setEditing(null); api.get<Profile[]>("/api/profiles").then(setProfiles); }}>{t("save")}</button></div>
                </div>
              )}
              {profiles.map((p) => (
                <div key={p.id} className="card row wrap">
                  <span className="grow col" style={{ gap: 0, flexBasis: 260 }}><strong>{lang === "ar" ? p.name_ar : p.name}</strong>
                    <span className="muted small">{p.focus.join(" · ")}</span></span>
                  <span className="muted small">{p.builtin ? t("builtin") : t("custom")}</span>
                  <button className="btn small" onClick={() => setEditing({ ...p, id: p.builtin ? undefined : p.id, name: p.builtin ? `${p.name} (custom)` : p.name })}>{t("edit")}</button>
                  {!p.builtin && <button className="icon-btn" aria-label={t("delete")} onClick={async () => {
                    await api.del(`/api/profiles/${p.id}`); api.get<Profile[]>("/api/profiles").then(setProfiles); }}><IconTrash /></button>}
                </div>
              ))}
            </>
          )}

          {section === "glossary" && (
            <>
              <p className="muted" style={{ margin: 0 }}>{t("glossaryHint")}</p>
              <div className="row wrap">
                <input className="input" style={{ flex: "1 1 200px" }} placeholder={t("english")} value={term.en} onChange={(e) => setTerm({ ...term, en: e.target.value })} />
                <input className="input" style={{ flex: "1 1 200px" }} dir="rtl" placeholder={t("arabic")} value={term.ar} onChange={(e) => setTerm({ ...term, ar: e.target.value })} />
                <button className="btn primary" disabled={!term.en.trim() || !term.ar.trim()} onClick={async () => {
                  await api.post("/api/glossary", term); setTerm({ en: "", ar: "" }); api.get<Term[]>("/api/glossary").then(setTerms); }}>{t("addTerm")}</button>
              </div>
              <div className="card" style={{ padding: 0 }}>
                {terms.map((g) => (
                  <div key={g.id} className="row" style={{ padding: "10px 14px", borderBottom: "1px solid var(--line2)" }}>
                    <span className="grow" dir="ltr">{g.en}</span><span className="grow" dir="rtl" style={{ fontWeight: 600 }}>{g.ar}</span>
                    <button className="icon-btn" aria-label={t("delete")} onClick={async () => { await api.del(`/api/glossary/${g.id}`); setTerms(terms.filter((x) => x.id !== g.id)); }}><IconTrash /></button>
                  </div>
                ))}
              </div>
            </>
          )}

          {section === "updates" && settings && (
            <>
              <button className="mode-card" style={{ justifyContent: "space-between" }} aria-pressed={settings.update_check}
                onClick={() => patch({ update_check: !settings.update_check })}>
                <span className="col" style={{ gap: 0 }}><strong>{t("checkUpdates")}</strong><span className="muted small">{t("checkUpdatesHint")}</span></span>
                <span className={`toggle ${settings.update_check ? "on" : ""}`} />
              </button>
              <div className="row"><button className="btn" onClick={async () => {
                const r = await api.get<{ available?: boolean; latest?: string; error?: string }>("/api/updates?force=true");
                setUpdateMsg(r.error ? r.error : r.available ? t("updateAvailable", { v: r.latest ?? "" }) : t("upToDate"));
              }}>{t("checkNow")}</button><span className="muted">{updateMsg}</span></div>
            </>
          )}

          {section === "about" && settings && (
            <>
              <label className="col" style={{ maxWidth: 360 }}><span>{t("yourName")}</span>
                <input className="input" defaultValue={settings.author} onBlur={(e) => patch({ author: e.target.value.trim() || "You" })} /></label>
              <div className="muted">GrepLens · AGPL-3.0 · github.com/swaidi/greplens</div>
              <div className="muted small">OCR by GrepAll · llama.cpp · LlamaIndex · LangGraph · Chroma · PyMuPDF · pdf.js</div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
