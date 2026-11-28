import { useEffect, useRef, useState } from "react";
import { api, appVersion, Profile, Workspace } from "../api";
import { useI18n } from "../i18n";
import { IconDownload, IconGear, Logo } from "../icons";
import { OnlineProblem } from "./OnlineAI";
import type { View } from "../App";

export type Update = { checked?: boolean; available?: boolean; latest?: string; asset?: string | null; error?: string; current?: string };
type Install = { status: string; done: number; total: number; message: string };

/** Shows an available update with Update now / Remind me later, and the download progress. */
export function UpdateOffer({ update, onDismiss, inline = false }: { update: Update; onDismiss?: () => void; inline?: boolean }) {
  const { t } = useI18n();
  const [inst, setInst] = useState<Install | null>(null);
  useEffect(() => {
    if (!inst || !["downloading", "starting"].includes(inst.status)) return;
    const timer = setInterval(() => api.get<Install>("/api/updates/progress").then(setInst), 700);
    return () => clearInterval(timer);
  }, [inst?.status]); // eslint-disable-line
  const start = async () => {
    try { setInst(await api.post<Install>("/api/updates/install", { asset: update.asset })); }
    catch (e) { setInst({ status: "error", done: 0, total: 0, message: String(e).replace(/^Error: /, "") }); }
  };
  const pct = inst?.total ? Math.round((inst.done / inst.total) * 100) : 0;
  return (
    <div className={inline ? "card col" : "banner"} role="status" style={inline ? { borderColor: "var(--accent-line)" } : undefined}>
      <span className="grow"><strong>{t("updateAvailable", { v: update.latest ?? "" })}</strong></span>
      {!inst && (
        <div className="row">
          <button className="btn primary" disabled={!update.asset} onClick={start}>{t("updateNow")}</button>
          <button className="btn" onClick={() => { api.post("/api/updates/later"); onDismiss?.(); }}>{t("remindLater")}</button>
        </div>
      )}
      {inst && inst.status === "downloading" && (
        <div className="col" style={{ minWidth: 240 }}>
          <span className="small">{t("updDownloading")} {pct}%</span>
          <div className="bar"><div style={{ width: `${pct}%` }} /></div>
        </div>
      )}
      {inst && ["starting", "started"].includes(inst.status) && <span className="small">{t("updStarting")}</span>}
      {inst && inst.status === "opened" && <span className="small">{t("updOpenedDeb")}</span>}
      {inst && inst.status === "error" && (
        <span className="small" style={{ color: "var(--danger-text)" }}>{inst.message}
          <button className="btn small" style={{ marginInlineStart: 8 }} onClick={start}>{t("retry")}</button></span>
      )}
    </div>
  );
}

export function UpdateBanner() {
  const [update, setUpdate] = useState<Update | null>(null);
  useEffect(() => { api.get<Update>("/api/updates").then(setUpdate).catch(() => {}); }, []);
  if (!update?.available) return null;
  return <UpdateOffer update={update} onDismiss={() => setUpdate(null)} />;
}

export function CategoryPicker({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const { lang } = useI18n();
  const [profiles, setProfiles] = useState<Profile[]>([]);
  useEffect(() => { api.get<Profile[]>("/api/profiles").then(setProfiles); }, []);
  const toggle = (id: string) => {
    if (value.includes(id)) onChange(value.filter((x) => x !== id));
    else onChange(value.length < 2 ? [...value, id] : [value[1], id]);
  };
  return (
    <div className="row wrap">
      {profiles.map((p) => (
        <button key={p.id} className={`chip ${value.includes(p.id) ? "on" : ""}`} aria-pressed={value.includes(p.id)}
          onClick={() => toggle(p.id)}>{lang === "ar" ? p.name_ar : p.name}</button>
      ))}
    </div>
  );
}

export default function Home({ go }: { go: (v: View) => void }) {
  const { t, lang } = useI18n();
  const [items, setItems] = useState<Workspace[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [cats, setCats] = useState<string[]>(["general"]);
  const [message, setMessage] = useState("");
  const [version, setVersion] = useState("");
  useEffect(() => { appVersion().then(setVersion); }, []);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = () => api.get<Workspace[]>("/api/workspaces").then(setItems);
  useEffect(() => { load(); api.get<Profile[]>("/api/profiles").then(setProfiles); }, []);
  const catName = (id: string) => { const p = profiles.find((x) => x.id === id); return p ? (lang === "ar" ? p.name_ar : p.name) : id; };

  const create = async () => {
    const ws = await api.post<Workspace>("/api/workspaces", { name, categories: cats });
    setCreating(false);
    go({ name: "workspace", id: ws.id });
  };
  const importPkg = async (file: File) => {
    try {
      const r = await api.upload<{ workspace_id: string; name: string; notes: number; documents: number }>("/api/packages/import", file);
      setMessage(`${r.name}: +${r.documents} ${t("documents")}, +${r.notes} ${t("notesCount")}`);
      load();
    } catch (e) { setMessage(String(e)); }
  };

  return (
    <div>
      <header className="topbar">
        <span className="brand"><Logo />GrepLens{version && <span className="muted small" style={{ fontWeight: 500, marginInlineStart: 6 }}>{version}</span>}</span>
        <span className="grow" />
        <button className="icon-btn" aria-label={t("settings")} onClick={() => go({ name: "settings" })}><IconGear /></button>
      </header>
      <OnlineProblem />
      <UpdateBanner />
      <main className="page">
        <div className="row wrap" style={{ alignItems: "flex-end", gap: 12 }}>
          <div className="grow" style={{ flexBasis: 300 }}>
            <h1 style={{ fontSize: 26, marginBottom: 4 }}>{t("workspaces")}</h1>
            <p className="muted" style={{ margin: 0 }}>{t("workspacesHint")}</p>
          </div>
          <input ref={fileRef} type="file" accept=".greplens,.zip" hidden
            onChange={(e) => { const f = e.target.files?.[0]; if (f) importPkg(f); e.target.value = ""; }} />
          <button className="btn big" onClick={() => fileRef.current?.click()}><IconDownload />{t("importPackage")}</button>
          <button className="btn big primary" onClick={() => { setName(""); setCats(["general"]); setCreating(true); }}>+ {t("newWorkspace")}</button>
        </div>
        {message && <div className="card small">{message}</div>}
        {items.length === 0 && <div className="card muted">{t("noWorkspaces")}</div>}
        <div className="grid-cards">
          {items.map((w) => (
            <button key={w.id} className="ws-card" onClick={() => go({ name: "workspace", id: w.id })}>
              <div className="row wrap">{w.categories.map((c) => <span key={c} className="pill">{catName(c)}</span>)}</div>
              <div style={{ fontSize: 17, fontWeight: 700 }}>{w.name}</div>
              <div className="row wrap muted small" style={{ gap: "6px 16px" }}>
                <span>{String(w.documents)} {t("documents")}</span>
                <span>{w.notes} {t("notesCount")}{(w.authors ?? 0) > 1 ? ` · ${w.authors} ${t("authors")}` : ""}</span>
                <span>{w.findings} {t("findingsCount")}</span>
              </div>
              <div className="muted small" style={{ paddingTop: 10, borderTop: "1px solid var(--line2)" }}>
                {new Date(w.updated * 1000).toLocaleDateString(lang === "ar" ? "ar-SA" : "en-GB")}
              </div>
            </button>
          ))}
        </div>
      </main>
      {creating && (
        <div className="modal-back" onClick={() => setCreating(false)}>
          <div className="modal" role="dialog" aria-label={t("newWorkspace")} onClick={(e) => e.stopPropagation()}>
            <h2 style={{ fontSize: 19 }}>{t("newWorkspace")}</h2>
            <label className="col"><span>{t("name")}</span>
              <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && name.trim() && create()} /></label>
            <div className="col"><span>{t("categories")} <span className="muted small">· {t("selectUpTo2")}</span></span>
              <CategoryPicker value={cats} onChange={setCats} /></div>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button className="btn" onClick={() => setCreating(false)}>{t("cancel")}</button>
              <button className="btn primary" disabled={!name.trim()} onClick={create}>{t("create")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
