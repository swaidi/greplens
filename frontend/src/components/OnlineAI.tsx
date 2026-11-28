import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { IconCheck } from "../icons";
import { Key, useI18n } from "../i18n";

export type OnlineStatus = {
  source: "local" | "online"; provider: string | null; provider_name: string | null;
  models: { fast?: string; review?: string }; available_models: string[]; key_hint: string;
  usage: { input: number; output: number; cost: number; requests: number; unpriced?: number };
  last_error: { message: string; can_use_local: boolean } | null;
  providers: Record<string, { name: string; company: string; key_url: string; steps: string[]; free_tier: boolean }>;
};
const ORDER = ["anthropic", "openai", "gemini", "openrouter"];

let statusCall: Promise<OnlineStatus> | null = null;
export const onlineStatus = (fresh = false) => {
  if (fresh || !statusCall) statusCall = api.get<OnlineStatus>("/api/ai/online");
  return statusCall;
};
export const openPage = (url: string) => api.post("/api/open-url", { url }).catch(() => window.open(url, "_blank"));

/** The two choices: AI on this computer, or an online service with the user's own key. */
export function AiSourcePicker({ status, onChange, recommendOnline, showConnected = true }: { status: OnlineStatus | null;
  onChange: (s: OnlineStatus) => void; recommendOnline?: boolean; showConnected?: boolean }) {
  const { t } = useI18n();
  const [wantOnline, setWantOnline] = useState(false);
  const [error, setError] = useState("");
  const online = status?.source === "online";
  const choose = async (source: "local" | "online") => {
    setError("");
    if (source === "online" && !status?.provider) { setWantOnline(true); return; }
    try { onChange(await api.post<OnlineStatus>("/api/ai/source", { source })); statusCall = null; setWantOnline(false); }
    catch (e) { setError(String(e).replace(/^Error: /, "")); }
  };
  return (
    <div className="col" style={{ gap: 10 }}>
      <div className="source-cards">
        <button className={`mode-card ${!online && !wantOnline ? "on" : ""}`} aria-pressed={!online && !wantOnline} onClick={() => choose("local")}>
          <span className="radio" /><span className="col" style={{ gap: 2 }}>
            <strong>{t("sourceLocal")}</strong><span className="muted small">{t("sourceLocalHint")}</span></span>
        </button>
        <button className={`mode-card ${online || wantOnline ? "on" : ""}`} aria-pressed={online || wantOnline} onClick={() => choose("online")}>
          <span className="radio" /><span className="col" style={{ gap: 2 }}>
            <strong>{t("sourceOnline")} {recommendOnline && <span className="pill ok" style={{ marginInlineStart: 6 }}>{t("recommendedHere")}</span>}</strong>
            <span className="muted small">{t("sourceOnlineHint")}</span></span>
        </button>
      </div>
      {error && <div className="error-box small">{error}</div>}
      {showConnected && online && status?.provider && !wantOnline && (
        <div className="card row" style={{ background: "var(--ok-bg)", borderColor: "var(--ok-line)" }}>
          <span style={{ color: "var(--ok-text)" }}><IconCheck size={20} /></span>
          <strong style={{ color: "var(--ok-text)" }}>{t("connectedReady", { s: status.provider_name ?? "" })}</strong>
        </div>
      )}
      {(wantOnline || (online && !status?.provider)) && status && (
        <OnlineConnect status={status} onConnected={(s) => { statusCall = null; setWantOnline(false); onChange(s); }} />
      )}
    </div>
  );
}

/** Choose a service, open its key page, paste the key: GrepLens recognizes and checks it. */
export function OnlineConnect({ status, onConnected, initial }: { status: OnlineStatus; onConnected: (s: OnlineStatus) => void; initial?: string }) {
  const { t } = useI18n();
  const [provider, setProvider] = useState<string | null>(initial ?? null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const keyBox = useRef<HTMLInputElement>(null);
  const p = provider ? status.providers[provider] : null;

  const connect = async (value = key) => {
    if (!value.trim() || busy) return;
    setBusy(true); setError("");
    try {
      const s = await api.post<OnlineStatus>("/api/ai/online/connect", { key: value, provider });
      setDone(s.provider_name); setKey("");
      onConnected(s);
    } catch (e) { setError(String(e).replace(/^Error: /, "")); }
    setBusy(false);
  };
  const detect = (value: string) => {
    const v = value.trim();
    return v.startsWith("sk-ant-") ? "anthropic" : v.startsWith("sk-or-") ? "openrouter" : v.startsWith("AIza") ? "gemini" : v.startsWith("sk-") ? "openai" : null;
  };

  if (done) {
    return (
      <div className="card row" style={{ background: "var(--ok-bg)", borderColor: "var(--ok-line)" }}>
        <span style={{ color: "var(--ok-text)" }}><IconCheck size={20} /></span>
        <strong style={{ color: "var(--ok-text)" }}>{t("connectedReady", { s: done })}</strong>
      </div>
    );
  }
  return (
    <div className="card col online-connect" style={{ gap: 14 }}>
      <strong>{t("chooseService")}</strong>
      <div className="provider-grid">
        {ORDER.filter((k) => status.providers[k]).map((k) => (
          <button key={k} className={`provider ${provider === k ? "on" : ""}`} aria-pressed={provider === k}
            onClick={() => { setProvider(k); setError(""); setTimeout(() => keyBox.current?.focus(), 50); }}>
            <ProviderMark id={k} />
            <span className="col" style={{ gap: 0, textAlign: "start" }}><strong>{status.providers[k].name}</strong>
              <span className="muted small">{status.providers[k].company}</span></span>
          </button>
        ))}
      </div>
      {p && (
        <>
          <ol className="key-steps">
            {p.steps.map((s, i) => (
              <li key={s}><StepPicture kind={s} /><span>
                <strong>{i + 1}.</strong> {t(`step_${s}` as Key, { c: p.company, s: p.name })}</span></li>
            ))}
          </ol>
          <div><button className="btn primary big" onClick={() => openPage(p.key_url)}>{t("getMyKey", { s: p.name })}</button></div>
          {p.free_tier && <div className="note-box small">{t("geminiFreeNote")}</div>}
        </>
      )}
      <label className="col" style={{ gap: 6 }}>
        <strong>{p ? t("pasteKeyHere") : t("alreadyHaveKey")}</strong>
        <div className="row">
          <input ref={keyBox} className="input grow" dir="ltr" type="password" autoComplete="off" spellCheck={false}
            placeholder={t("pasteKeyPlaceholder")} value={key} aria-label={t("pasteKeyHere")}
            onChange={(e) => { setKey(e.target.value); const d = detect(e.target.value); if (d && !provider) setProvider(d); }}
            onPaste={(e) => {
              const v = e.clipboardData.getData("text");
              if (v.trim().length > 20) { e.preventDefault(); setKey(v); const d = detect(v); if (d && (!provider || d !== provider)) setProvider(d); setTimeout(() => connect(v), 0); }
            }}
            onKeyDown={(e) => e.key === "Enter" && connect()} />
          <button className="btn big" disabled={busy} onClick={async () => {
            try { const v = await navigator.clipboard.readText(); setKey(v); const d = detect(v); if (d) setProvider(d); connect(v); }
            catch { keyBox.current?.focus(); }
          }}>{t("paste")}</button>
          <button className="btn big primary" disabled={!key.trim() || busy} onClick={() => connect()}>{busy ? t("checking") : t("connect")}</button>
        </div>
      </label>
      {error && <div className="error-box">{error}</div>}
      <span className="muted small">{t("onlinePrivacy")}</span>
    </div>
  );
}

/** Settings > AI service: where the AI runs, the connected service, this month's use, and the models (advanced). */
export function OnlineSettings() {
  const { t } = useI18n();
  const [status, setStatus] = useState<OnlineStatus | null>(null);
  const [changing, setChanging] = useState(false);
  useEffect(() => { onlineStatus(true).then(setStatus).catch(() => {}); }, []);
  if (!status) return null;
  const u = status.usage;
  return (
    <div className="col" style={{ gap: 14 }}>
      <h2 style={{ fontSize: 20 }}>{t("aiService")}</h2>
      <p className="muted" style={{ margin: 0 }}>{t("aiServiceHint")}</p>
      <AiSourcePicker status={status} onChange={setStatus} showConnected={false} />
      {status.provider && (
        <div className="card col" style={{ gap: 10 }}>
          <div className="row wrap" style={{ justifyContent: "space-between" }}>
            <span className="row"><ProviderMark id={status.provider} /><span className="col" style={{ gap: 0 }}>
              <strong>{t("connectedTo", { s: status.provider_name ?? "" })}</strong>
              <span className="muted small" dir="ltr">{status.key_hint}</span></span></span>
            <span className="row">
              <button className="btn" onClick={() => setChanging(!changing)}>{t("changeKey")}</button>
              <button className="btn" onClick={async () => { setStatus(await api.post<OnlineStatus>("/api/ai/online/disconnect")); setChanging(false); }}>
                {t("disconnect")}</button>
            </span>
          </div>
          {changing && <OnlineConnect status={status} initial={status.provider} onConnected={(s) => { setStatus(s); setChanging(false); }} />}
          <div className="row wrap" style={{ gap: 18 }}>
            <span><span className="muted small">{t("thisMonth")}</span><br /><strong>{u.cost > 0 || !u.unpriced ? t("aboutCost", { c: u.cost < 0.01 && u.requests ? "<0.01" : u.cost.toFixed(2) }) : "—"}</strong></span>
            <span><span className="muted small">{t("requests")}</span><br /><strong>{u.requests}</strong></span>
            <span><span className="muted small">{t("textSent")}</span><br /><strong>{((u.input + u.output) / 1000).toFixed(0)}k {t("tokens")}</strong></span>
          </div>
          <span className="muted small">{t("costNote")}</span>
          <details>
            <summary className="small">{t("advanced")}</summary>
            <div className="col" style={{ gap: 8, marginTop: 8 }}>
              {(["fast", "review"] as const).map((role) => (
                <label key={role} className="row small" style={{ justifyContent: "space-between" }}>
                  <span>{t(role === "fast" ? "modelForQuestions" : "modelForReview")}</span>
                  <select className="input" style={{ width: "auto", minHeight: 34 }} dir="ltr" value={status.models[role] ?? ""}
                    onChange={async (e) => setStatus(await api.patch<OnlineStatus>("/api/ai/online/models", { [role]: e.target.value }))}>
                    {status.available_models.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </label>
              ))}
            </div>
          </details>
        </div>
      )}
    </div>
  );
}

/** When the online service fails: a plain message, and a way back to this computer's AI when it is installed. */
export function OnlineProblem() {
  const { t } = useI18n();
  const [status, setStatus] = useState<OnlineStatus | null>(null);
  const [hidden, setHidden] = useState("");
  useEffect(() => {
    let live = true;
    const load = () => onlineStatus(true).then((s) => live && setStatus(s)).catch(() => {});
    load();
    const timer = setInterval(load, 15000);
    return () => { live = false; clearInterval(timer); };
  }, []);
  const err = status?.source === "online" ? status.last_error : null;
  if (!err || hidden === err.message) return null;
  return (
    <div className="online-problem" role="alert">
      <span className="grow">{err.message}</span>
      {err.can_use_local && <button className="btn small" onClick={async () => { setStatus(await api.post<OnlineStatus>("/api/ai/source", { source: "local" })); }}>
        {t("useLocalInstead")}</button>}
      <button className="btn small" onClick={() => setHidden(err.message)}>{t("dismiss")}</button>
    </div>
  );
}

function ProviderMark({ id }: { id: string }) {
  const color = { anthropic: "#C96442", openai: "#10A37F", gemini: "#4285F4", openrouter: "#6467F2" }[id] ?? "#888";
  const letter = { anthropic: "C", openai: "G", gemini: "G", openrouter: "O" }[id] ?? "?";
  return <span className="provider-mark" style={{ background: color }} aria-hidden="true">{letter}</span>;
}

function StepPicture({ kind }: { kind: string }) {
  const common = { width: 44, height: 44, viewBox: "0 0 48 48", fill: "none", stroke: "currentColor", strokeWidth: 2.2,
    strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true, className: "step-pic" };
  if (kind === "addCredit") return <svg {...common}><rect x="6" y="12" width="36" height="24" rx="4" /><path d="M6 20h36M12 30h8" /></svg>;
  if (kind === "createKey") return <svg {...common}><circle cx="16" cy="24" r="7" /><path d="M23 24h19M36 24v6M41 24v4" /></svg>;
  if (kind === "copyKey") return <svg {...common}><rect x="16" y="14" width="22" height="26" rx="3" /><path d="M10 34V10a2 2 0 0 1 2-2h18" /></svg>;
  return <svg {...common}><circle cx="24" cy="18" r="7" /><path d="M10 40c2-8 8-11 14-11s12 3 14 11" /></svg>;
}
