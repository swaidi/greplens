import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Key, useI18n } from "../i18n";
import { IconCheck, Logo } from "../icons";

type Hardware = {
  cpu: string; cores: number; threads: number; base_ghz: number | null; ram_gb: number; gpus: string[];
  dedicated_gpu: string | null; os: string; bits: string; drives: { mount: string; free_gb: number; total_gb: number }[];
  recommendation: { models: string[]; features: { id: string; status: string; speed: string }[] };
  measured: Record<string, number>;
};
type ManifestModel = { id: string; role: string; name: string; use: string; size_gb: number };
export type Existing = { model_id: string; role?: string; path: string; source: string; label: string; ollama_name?: string; size_gb: number };
type ModelsInfo = { manifest: ManifestModel[]; installed: Record<string, { id: string }>; existing: Existing[] };
type Progress = { paused: boolean; items: Record<string, { status: string; done_bytes: number; total: number; speed: number; message: string }> };

export default function Setup({ onDone }: { onDone: () => void }) {
  const { t, lang, setLang } = useI18n();
  const [step, setStep] = useState(1);
  const [hw, setHw] = useState<Hardware | null>(null);
  const [info, setInfo] = useState<ModelsInfo | null>(null);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [reused, setReused] = useState<Record<string, Existing>>({});
  const [folder, setFolder] = useState("");
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState("");
  const [scanFolder, setScanFolder] = useState("");
  const [scanMsg, setScanMsg] = useState("");

  useEffect(() => {
    api.get<Hardware>("/api/hardware").then((h) => {
      setHw(h);
      setPicked(Object.fromEntries(h.recommendation.models.map((m) => [m, true])));
      const other = h.drives.filter((d) => !/^[Cc]:/.test(d.mount)).sort((a, b) => b.free_gb - a.free_gb)[0];
      const drive = other?.mount ?? h.drives[0]?.mount ?? "";
      const win = /^[A-Za-z]:/.test(drive);
      setFolder(win ? `${drive.replace(/[\\/]$/, "")}\\GrepLens\\Models` : `${drive === "/" ? "~" : drive}/GrepLens/models`);
    }).catch((e) => setError(String(e)));
    api.get<ModelsInfo>("/api/models").then(setInfo).catch(() => {});
  }, []);

  useEffect(() => {
    if (step !== 4) return;
    const timer = setInterval(() => api.get<Progress>("/api/models/progress").then(setProgress), 1000);
    return () => clearInterval(timer);
  }, [step]);

  const chosen = useMemo(() => (info?.manifest ?? []).filter((m) => picked[m.id]), [info, picked]);
  const toDownload = chosen.filter((m) => !reused[m.id]);
  const totalGb = toDownload.reduce((a, m) => a + m.size_gb, 0);
  const drive = hw?.drives.filter((d) => folder.toLowerCase().startsWith(d.mount.toLowerCase()))
    .sort((a, b) => b.mount.length - a.mount.length)[0];
  const allDone = toDownload.every((m) => progress?.items[m.id]?.status === "done");

  const reuse = async (e: Existing) => {
    await api.post("/api/models/reuse", { model_id: e.model_id, path: e.path, source: e.source, ollama_name: e.ollama_name });
    setReused((r) => ({ ...r, [e.model_id]: e }));
  };
  const scan = async () => {
    setScanMsg("");
    try {
      const r = await api.post<{ existing: Existing[] }>("/api/models/scan", { folder: scanFolder });
      setInfo((i) => i ? { ...i, existing: r.existing } : i);
      setFolder(scanFolder.trim());
      const found = r.existing.filter((e) => e.path.toLowerCase().startsWith(scanFolder.trim().toLowerCase())).length;
      setScanMsg(t("scanResult", { n: found }));
    } catch (e) { setScanMsg(String(e).replace(/^Error: /, "")); }
  };
  const startDownload = async () => {
    setError("");
    try {
      await api.patch("/api/settings", { models_dir: folder.trim() }); // remember the folder even when nothing downloads
      if (toDownload.length) setProgress(await api.post<Progress>("/api/models/download", { ids: toDownload.map((m) => m.id), folder }));
      setStep(4);
    } catch (e) { setError(String(e)); }
  };
  const finish = async () => { await api.patch("/api/settings", { setup_complete: true }); onDone(); };
  const status = (s: string) => ({ rec: ["ok", t("rec")], slow: ["warn", t("slow")], no: ["high", t("no")] } as Record<string, string[]>)[s];
  const steps: Key[] = ["deviceCheck", "models", "storage", "downloadStep"];
  const gb = (bytes: number) => (bytes / 1024 ** 3).toFixed(1);

  return (
    <div className="setup-shell">
      <div className="setup-box">
        <nav className="setup-nav" aria-label={t("setupTitle")}>
          <div className="row" style={{ fontSize: 18, fontWeight: 700 }}><Logo />GrepLens</div>
          <div className="col" style={{ gap: 6 }}>
            {steps.map((k, i) => (
              <button key={k} className={`step ${step === i + 1 ? "on" : ""} ${step > i + 1 ? "done" : ""}`}
                onClick={() => i + 1 < step && setStep(i + 1)}><span className="num">{i + 1}</span>{t(k)}</button>
            ))}
          </div>
          <div className="seg" style={{ marginTop: "auto", background: "#1D4C55" }}>
            <button className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>EN</button>
            <button className={lang === "ar" ? "on" : ""} onClick={() => setLang("ar")}>عربي</button>
          </div>
        </nav>

        <section className="setup-body">
          {error && <div className="error-box">{error}</div>}
          {step === 1 && hw && (
            <>
              <div><h1 style={{ fontSize: 24, marginBottom: 4 }}>{t("yourDevice")}</h1><p className="muted" style={{ margin: 0 }}>{t("deviceHint")}</p></div>
              <div className="grid2">
                <div className="card"><div className="muted small">{t("processor")}</div><div style={{ fontWeight: 600 }}>{hw.cpu}</div>
                  <div className="muted small">{hw.cores} cores · {hw.threads} threads{hw.base_ghz ? ` · ${hw.base_ghz} GHz` : ""}</div></div>
                <div className="card"><div className="muted small">{t("memory")}</div><div style={{ fontWeight: 600 }}>{hw.ram_gb} GB</div></div>
                <div className="card"><div className="muted small">{t("graphics")}</div>
                  <div style={{ fontWeight: 600 }}>{hw.dedicated_gpu ?? (hw.gpus[0] || t("noneDetected"))}</div>
                  <div className="muted small">{t("runsOnCpu")}</div></div>
                <div className="card"><div className="muted small">{t("os")}</div><div style={{ fontWeight: 600 }}>{hw.os} · {hw.bits}</div>
                  <div className="muted small">{t("freeSpace")}: {hw.drives.map((d) => `${d.mount} ${d.free_gb} GB`).join(" · ")}</div></div>
              </div>
              <div className="card col" style={{ borderColor: "var(--accent-line)", background: "#F2F9FA" }}>
                <strong style={{ color: "var(--accent-dark)" }}>{t("recommended")}</strong>
                <div>{(info?.manifest ?? []).filter((m) => hw.recommendation.models.includes(m.id)).map((m) => `${m.name} (${m.use})`).join(" · ")}</div>
                {hw.recommendation.features.map((f) => {
                  const [cls, label] = status(f.status);
                  return (
                    <div key={f.id} className="row" style={{ justifyContent: "space-between", padding: "8px 10px", background: "#fff", borderRadius: 8 }}>
                      <span className="col" style={{ gap: 0 }}><strong>{t(`feat_${f.id}` as Key)}</strong>
                        {f.speed && <span className="muted small">{f.speed}</span>}</span>
                      <span className={`pill ${cls}`}>{label}</span>
                    </div>
                  );
                })}
                <span className="muted small">{t("speedNote")}</span>
              </div>
            </>
          )}

          {step === 2 && info && (
            <>
              <div><h1 style={{ fontSize: 24, marginBottom: 4 }}>{t("chooseModels")}</h1><p className="muted" style={{ margin: 0 }}>{t("chooseModelsHint")}</p></div>
              <div className="card col" style={{ background: "#F6F8FA" }}>
                <strong>{t("haveFiles")}</strong>
                <span className="muted small">{t("haveFilesHint")}</span>
                <div className="row">
                  <input className="input grow" dir="ltr" placeholder="D:\dev\GrepLens\Models" value={scanFolder}
                    onChange={(e) => setScanFolder(e.target.value)} onKeyDown={(e) => e.key === "Enter" && scanFolder.trim() && scan()} />
                  <button className="btn big" disabled={!scanFolder.trim()} onClick={scan}>{t("scanFolder")}</button>
                </div>
                {scanMsg && <span className="small">{scanMsg}</span>}
              </div>
              {info.existing.filter((e) => picked[e.model_id]).map((e) => (
                <div key={e.path} className="row card" style={{ background: "#EEF6EE", borderColor: "#BFDFC4" }}>
                  <span style={{ color: "#1F6B3A" }}><IconCheck size={18} /></span>
                  <div className="grow"><strong style={{ color: "#1F6B3A" }}>{t("existingFound", { s: e.source, n: e.label })}</strong>
                    <div className="small">{t("reuseHint")}</div></div>
                  <button className="btn" disabled={!!reused[e.model_id]} onClick={() => reuse(e)}>{reused[e.model_id] ? t("reused") : t("reuse")}</button>
                </div>
              ))}
              {info.manifest.map((m) => (
                <button key={m.id} className={`pick ${picked[m.id] ? "on" : ""}`} aria-pressed={!!picked[m.id]}
                  onClick={() => setPicked((p) => ({ ...p, [m.id]: !p[m.id] }))}>
                  <span className="check" />
                  <span className="grow col" style={{ gap: 2 }}><strong>{m.name}</strong><span className="muted small">{m.use}</span></span>
                  <strong>{reused[m.id] ? t("reused") : `≈ ${m.size_gb} GB`}</strong>
                </button>
              ))}
              <span className="muted small">{t("totalDownload", { g: totalGb.toFixed(1) })}</span>
            </>
          )}

          {step === 3 && (
            <>
              <div><h1 style={{ fontSize: 24, marginBottom: 4 }}>{t("whereToSave")}</h1><p className="muted" style={{ margin: 0 }}>{t("whereHint")}</p></div>
              <label className="col"><strong>{t("modelsFolder")}</strong>
                <input className="input" dir="ltr" value={folder} onChange={(e) => setFolder(e.target.value)} /></label>
              {drive && (
                <div className="card col">
                  <div className="row" style={{ justifyContent: "space-between" }}><strong>{drive.mount}</strong>
                    <span className="muted">{drive.free_gb} / {drive.total_gb} GB</span></div>
                  <div className="bar"><div style={{ width: `${100 - (drive.free_gb / drive.total_gb) * 100}%`, background: "#8A94A3" }} /></div>
                  <span style={{ color: drive.free_gb > totalGb + 2 ? "#1F6B3A" : "#A1261B" }}>
                    {drive.free_gb > totalGb + 2 ? t("enoughSpace") : t("notEnoughSpace")}</span>
                </div>
              )}
            </>
          )}

          {step === 4 && (
            <>
              <div><h1 style={{ fontSize: 24, marginBottom: 4 }}>{t("downloading")}</h1><p className="muted" style={{ margin: 0 }}>{t("downloadHint")}</p></div>
              {chosen.map((m) => {
                const st = progress?.items[m.id];
                const pct = reused[m.id] || st?.status === "done" ? 100 : st?.total ? (st.done_bytes / st.total) * 100 : 0;
                const label = reused[m.id] ? t("reused") : st ? (st.status === "downloading" ? `${pct.toFixed(0)}%` :
                  t((({ done: "verified", paused: "paused", queued: "queued", verifying: "verifying", error: "error" } as Record<string, Key>)[st.status]) ?? "queued")) : "";
                return (
                  <div key={m.id} className="card col">
                    <div className="row" style={{ justifyContent: "space-between" }}><strong>{m.name}</strong><strong style={{ color: "var(--accent-dark)" }}>{label}</strong></div>
                    <div className="bar"><div style={{ width: `${pct}%`, background: pct === 100 ? "#2E9D5B" : undefined }} /></div>
                    <span className="muted small">{reused[m.id] ? reused[m.id].path : st ? `${gb(st.done_bytes)} / ${gb(st.total)} GB${st.speed ? ` · ${(st.speed / 1024 ** 2).toFixed(1)} MB/s` : ""}${st.message ? ` · ${st.message}` : ""}` : ""}</span>
                  </div>
                );
              })}
              {toDownload.length > 0 && !allDone && (
                <div><button className="btn big" onClick={async () => setProgress(await api.post<Progress>("/api/models/pause", { paused: !progress?.paused }))}>
                  {progress?.paused ? t("resume") : t("pause")}</button></div>
              )}
            </>
          )}

          <div className="row" style={{ marginTop: "auto", justifyContent: "space-between", paddingTop: 16, borderTop: "1px solid var(--line2)" }}>
            <button className="btn big" disabled={step === 1} onClick={() => setStep(step - 1)}>{t("back")}</button>
            {step < 3 && <button className="btn big primary" onClick={() => setStep(step + 1)}>{step === 1 ? t("accept") : t("next")}</button>}
            {step === 3 && <button className="btn big primary" disabled={!folder.trim() && toDownload.length > 0} onClick={startDownload}>{t("startDownload")}</button>}
            {step === 4 && <button className="btn big primary" disabled={!allDone} onClick={finish}>{t("finish")}</button>}
          </div>
        </section>
      </div>
    </div>
  );
}
