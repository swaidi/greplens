import { useEffect, useMemo, useState } from "react";
import { api, Doc, download, Workspace } from "../api";
import { Key, useI18n } from "../i18n";
import { IconClose, IconDownload } from "../icons";
import type { Job } from "./Tools";

type Cite = { document_id: string; document: string; location: string; page: number; segment_id: string };
type JumpDoc = (documentId: string, segmentId: string | null, page?: number) => void;
type Kind = "briefing" | "study_guide" | "flashcards" | "comparison" | "timeline" | "mindmap" | "literature_review" | "slides";

const KINDS: { id: Kind; label: Key; desc: Key; digest: boolean }[] = [
  { id: "briefing", label: "cBriefing", desc: "cBriefingDesc", digest: true },
  { id: "study_guide", label: "cStudy", desc: "cStudyDesc", digest: true },
  { id: "flashcards", label: "cFlash", desc: "cFlashDesc", digest: true },
  { id: "comparison", label: "cCompare", desc: "cCompareDesc", digest: false },
  { id: "timeline", label: "cTimeline", desc: "cTimelineDesc", digest: false },
  { id: "mindmap", label: "cMindmap", desc: "cMindmapDesc", digest: true },
  { id: "literature_review", label: "cLitReview", desc: "cLitReviewDesc", digest: true },
  { id: "slides", label: "cSlides", desc: "cSlidesDesc", digest: true },
];
const RUNNING = ["queued", "running", "cancelling"];

/** Create: outputs built from several documents of the workspace; every point links back to its source. */
export default function CreatePanel({ workspace, onJumpDoc }: { workspace: Workspace; onJumpDoc: JumpDoc }) {
  const { t } = useI18n();
  const docs = useMemo(() => (Array.isArray(workspace.documents) ? workspace.documents as Doc[] : []).filter((d) => d.status === "ready"),
    [workspace.documents]);
  const [kind, setKind] = useState<Kind>("briefing");
  const [picked, setPicked] = useState<string[] | null>(null);
  const [depth, setDepth] = useState<"full" | "quick">("full");
  const [focus, setFocus] = useState("");
  const [count, setCount] = useState(12);
  const [criteria, setCriteria] = useState("");
  const [language, setLanguage] = useState("auto");
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [error, setError] = useState("");
  const [view, setView] = useState<Job | null>(null);
  const chosen = picked ?? docs.map((d) => d.id);
  const meta = KINDS.find((k) => k.id === kind)!;
  const job = jobs[kind];
  const running = !!job && RUNNING.includes(job.status);

  const load = () => api.get<Record<string, Job>>(`/api/workspaces/${workspace.id}/create`).then(setJobs).catch(() => {});
  useEffect(() => { load(); }, [workspace.id]); // eslint-disable-line
  useEffect(() => {
    const live = Object.values(jobs).filter((j) => RUNNING.includes(j.status));
    if (!live.length) return;
    const timer = setInterval(async () => {
      const updated = await Promise.all(live.map((j) => api.get<Job>(`/api/jobs/${j.id}`)));
      setJobs((cur) => ({ ...cur, ...Object.fromEntries(updated.map((u) => [u.kind.replace(/^create_/, ""), u])) }));
    }, 1500);
    return () => clearInterval(timer);
  }, [jobs]);

  const run = async () => {
    setError("");
    const params: Record<string, unknown> = { document_ids: chosen, depth, focus, language };
    if (kind === "flashcards" || kind === "slides") params.count = count;
    if (kind === "comparison") params.criteria = criteria.split("\n").map((x) => x.trim()).filter(Boolean);
    try { const j = await api.post<Job>(`/api/workspaces/${workspace.id}/create`, { kind, params }); setJobs({ ...jobs, [kind]: j }); }
    catch (e) { setError(String(e).replace(/^Error: /, "")); }
  };

  const canRun = chosen.length >= (kind === "comparison" ? 2 : 1) && !running;
  return (
    <div className="col" style={{ flex: "1 1 auto", minHeight: 0, overflow: "auto", padding: 14, gap: 14 }}>
      <p className="muted small" style={{ margin: 0 }}>{t("createHint")}</p>
      <div className="create-grid">
        {KINDS.map((k) => (
          <button key={k.id} className={`create-card ${kind === k.id ? "on" : ""}`} aria-pressed={kind === k.id} onClick={() => setKind(k.id)}>
            <strong>{t(k.label)}</strong><span className="muted small">{t(k.desc)}</span>
            {jobs[k.id]?.status === "done" && <span className="pill ok">{t("ready")}</span>}
            {jobs[k.id] && RUNNING.includes(jobs[k.id].status) && <span className="pill warn">{t("working")}</span>}
          </button>
        ))}
      </div>

      <div className="col card" style={{ background: "var(--soft2)", gap: 10 }}>
        <span className="small"><strong>{t("sources")}</strong> · {t("nSelected", { n: chosen.length })}</span>
        {!docs.length && <span className="muted small">{t("noDocuments")}</span>}
        <div className="col" style={{ gap: 4, maxHeight: 150, overflow: "auto" }}>
          {docs.map((d) => (
            <label key={d.id} className="row small" style={{ gap: 8 }}>
              <input type="checkbox" checked={chosen.includes(d.id)}
                onChange={(e) => setPicked(e.target.checked ? [...chosen, d.id] : chosen.filter((x) => x !== d.id))} />
              <span dir="auto" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.filename}</span>
            </label>
          ))}
        </div>
        {kind === "comparison" && (
          <label className="col"><span className="small">{t("criteriaLabel")}</span>
            <textarea className="input" rows={4} dir="auto" placeholder={t("criteriaPlaceholder")} value={criteria}
              onChange={(e) => setCriteria(e.target.value)} /></label>
        )}
        {meta.digest && (
          <label className="col"><span className="small">{t("focusLabel")}</span>
            <input className="input" dir="auto" placeholder={t("focusPlaceholder")} value={focus} onChange={(e) => setFocus(e.target.value)} /></label>
        )}
        {(kind === "flashcards" || kind === "slides") && (
          <label className="row small" style={{ gap: 8 }}>{kind === "slides" ? t("slideCount") : t("cardCount")}
            <input className="input" type="number" min={4} max={kind === "slides" ? 25 : 30} style={{ width: 80 }} value={count}
              onChange={(e) => setCount(Number(e.target.value) || 10)} /></label>
        )}
        <div className="row wrap" style={{ gap: 8 }}>
          {meta.digest && (
            <div className="seg" role="group" aria-label={t("depth")}>
              <button className={depth === "full" ? "on" : ""} onClick={() => setDepth("full")}>{t("thorough")}</button>
              <button className={depth === "quick" ? "on" : ""} onClick={() => setDepth("quick")}>{t("quick")}</button>
            </div>
          )}
          <select className="input" style={{ width: "auto", minHeight: 34 }} aria-label={t("outputLanguage")} value={language}
            onChange={(e) => setLanguage(e.target.value)}>
            <option value="auto">{t("langAuto")}</option><option value="en">English</option><option value="ar">العربية</option>
          </select>
        </div>
        {meta.digest && <span className="muted small">{depth === "quick" ? t("quickHint") : t("thoroughHint")}</span>}
      </div>

      <button className="btn big primary" disabled={!canRun} onClick={run}>{t("createRun", { o: t(meta.label) })}</button>
      {error && <div className="error-box small">{error}</div>}
      {job && (
        <div className="card col" style={{ padding: "10px 12px", gap: 8 }}>
          {running && (
            <>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="small">{job.message || t("working")}</span>
                <button className="btn small" onClick={() => api.post(`/api/jobs/${job.id}/cancel`).then(load)}>{t("stop")}</button>
              </div>
              <div className="bar"><div style={{ width: `${Math.max(3, job.progress * 100)}%` }} /></div>
            </>
          )}
          {job.status === "error" && <div className="error-box small">{job.message}</div>}
          {job.status === "cancelled" && <span className="muted small">{t("stopped")}</span>}
          {job.status === "done" && (
            <div className="row wrap">
              <strong className="grow">{t(meta.label)}</strong>
              <button className="btn primary small" onClick={() => setView(job)}>{t("openResult")}</button>
              <button className="btn small" onClick={() => download(`/api/jobs/${job.id}/file`)}><IconDownload />{t("exportWord")}</button>
            </div>
          )}
        </div>
      )}
      {view && <ResultView job={view} onClose={() => setView(null)} onJumpDoc={(d, s, p) => { setView(null); onJumpDoc(d, s, p); }} />}
    </div>
  );
}

function Sources({ cites, onJumpDoc }: { cites?: Cite[]; onJumpDoc: JumpDoc }) {
  if (!cites?.length) return null;
  return (
    <span className="row wrap" style={{ gap: 4, marginTop: 4 }}>
      {cites.map((c, i) => (
        <button key={i} className="cite" dir="auto" onClick={() => onJumpDoc(c.document_id, c.segment_id, c.page)}>
          {c.document} · {c.location} →</button>
      ))}
    </span>
  );
}

type R = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

function ResultView({ job, onClose, onJumpDoc }: { job: Job; onClose: () => void; onJumpDoc: JumpDoc }) {
  const { t } = useI18n();
  const r = (job.result ?? {}) as R;
  const kind = String(r.kind ?? "");
  const [flipped, setFlipped] = useState<Record<number, boolean>>({});
  const [answers, setAnswers] = useState<Record<number, number>>({});
  const S = (c?: Cite[]) => <Sources cites={c} onJumpDoc={onJumpDoc} />;
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal result-modal" role="dialog" aria-label={String(r.title ?? "")} onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between", position: "sticky", top: -22, background: "var(--panel)", paddingBlock: 6, zIndex: 1 }}>
          <h2 style={{ fontSize: 19 }} dir="auto">{r.title}</h2>
          <div className="row">
            <button className="btn small" onClick={() => download(`/api/jobs/${job.id}/file`)}><IconDownload />{t("exportWord")}</button>
            <button className="icon-btn" aria-label={t("close")} onClick={onClose}><IconClose /></button>
          </div>
        </div>
        <span className="muted small">{t("sources")}: {(r.documents ?? []).map((d: R) => d.name).join(" · ")}
          {r.provider && r.provider !== "local" ? ` · ${t("cloudOn")}: ${r.provider}` : ""}</span>

        {kind === "briefing" && <>
          {r.overview && <p dir="auto" style={{ margin: 0 }}>{r.overview}</p>}
          {(r.sections ?? []).map((s: R, i: number) => (
            <section key={i} className="col" style={{ gap: 6 }}><h3 dir="auto">{s.heading}</h3>
              {s.points.map((p: R, j: number) => <div key={j} className="result-item" dir="auto">{p.text}{S(p.sources)}</div>)}</section>))}
          {(r.key_values ?? []).length > 0 && <section className="col"><h3>{t("keyValues")}</h3>
            <table className="result-table"><tbody>{r.key_values.map((k: R, i: number) => (
              <tr key={i}><td dir="auto">{k.item}</td><td dir="auto"><strong>{k.value}</strong></td><td>{S(k.sources)}</td></tr>))}</tbody></table></section>}
          {(r.open_questions ?? []).length > 0 && <section className="col"><h3>{t("openQuestions")}</h3>
            {r.open_questions.map((q: R, i: number) => <div key={i} className="result-item" dir="auto">{q.text}{S(q.sources)}</div>)}</section>}
        </>}

        {kind === "study_guide" && <>
          {r.summary && <p dir="auto" style={{ margin: 0 }}>{r.summary}</p>}
          {(r.concepts ?? []).length > 0 && <section className="col"><h3>{t("keyConcepts")}</h3>
            {r.concepts.map((c: R, i: number) => <div key={i} className="result-item" dir="auto"><strong>{c.term}</strong> — {c.explanation}{S(c.sources)}</div>)}</section>}
          {(r.faq ?? []).length > 0 && <section className="col"><h3>{t("faq")}</h3>
            {r.faq.map((f: R, i: number) => <div key={i} className="result-item" dir="auto"><strong>{f.question}</strong><div>{f.answer}</div>{S(f.sources)}</div>)}</section>}
          {(r.glossary ?? []).length > 0 && <section className="col"><h3>{t("studyGlossary")}</h3>
            <table className="result-table"><tbody>{r.glossary.map((g: R, i: number) => (
              <tr key={i}><td dir="auto"><strong>{g.term}</strong></td><td dir="auto">{g.definition}</td></tr>))}</tbody></table></section>}
        </>}

        {kind === "flashcards" && <>
          {(r.flashcards ?? []).length > 0 && <section className="col"><h3>{t("flashcards")}</h3><span className="muted small">{t("flipHint")}</span>
            <div className="flash-grid">{r.flashcards.map((c: R, i: number) => (
              <button key={i} className={`flash-card ${flipped[i] ? "back" : ""}`} dir="auto" onClick={() => setFlipped({ ...flipped, [i]: !flipped[i] })}>
                {flipped[i] ? c.back : c.front}</button>))}</div></section>}
          {(r.quiz ?? []).length > 0 && <section className="col"><h3>{t("quiz")}</h3>
            {r.quiz.map((q: R, i: number) => (
              <div key={i} className="result-item col" style={{ gap: 6 }}>
                <strong dir="auto">{i + 1}. {q.question}</strong>
                {q.options.map((o: string, k: number) => {
                  const a = answers[i];
                  const state = a === undefined ? "" : k === q.answer ? "right" : k === a ? "wrong" : "";
                  return <button key={k} className={`quiz-opt ${state}`} disabled={a !== undefined} dir="auto"
                    onClick={() => setAnswers({ ...answers, [i]: k })}>{o}</button>;
                })}
                {answers[i] !== undefined && <span className="small" dir="auto">{q.explanation}{S(q.sources)}</span>}
              </div>))}
            <span className="muted small">{t("quizScore", { s: String(Object.entries(answers).filter(([i, a]) => r.quiz[Number(i)].answer === a).length), n: String(r.quiz.length) })}</span>
          </section>}
        </>}

        {kind === "comparison" && (
          <div style={{ overflow: "auto" }}>
            <table className="result-table"><thead><tr><th>{t("criterion")}</th>{(r.documents ?? []).map((d: R) => <th key={d.id} dir="auto">{d.name}</th>)}</tr></thead>
              <tbody>{(r.rows ?? []).map((row: R, i: number) => (
                <tr key={i}><td dir="auto"><strong>{row.criterion}</strong></td>
                  {row.cells.map((c: R, j: number) => <td key={j} dir="auto">{c.found ? c.text : <span className="muted">{t("notFoundShort")}</span>}{S(c.sources)}</td>)}</tr>))}
              </tbody></table>
          </div>
        )}

        {kind === "timeline" && (
          (r.events ?? []).length === 0 ? <span className="muted">{t("noEvents")}</span> :
            <div className="timeline">{r.events.map((e: R, i: number) => (
              <div key={i} className={`tl-item ${e.dated ? "" : "undated"}`}><span className="tl-date" dir="auto">{e.date}</span>
                <div dir="auto">{e.event}{S(e.sources)}</div></div>))}</div>
        )}

        {kind === "mindmap" && <>
          <div className="mm-root">{r.title}</div>
          <div className="mm-branches">{(r.children ?? []).map((c: R, i: number) => (
            <div key={i} className="mm-branch"><strong className="mm-node" dir="auto">{c.title}</strong>
              <ul>{(c.children ?? []).map((g: R, j: number) => (
                <li key={j} dir="auto">{g.segment_id
                  ? <button className="mm-node" style={{ fontWeight: 400 }} onClick={() => onJumpDoc(g.document_id, g.segment_id, g.page)}>{g.title}</button>
                  : g.title}</li>))}</ul></div>))}</div>
        </>}

        {kind === "literature_review" && <>
          {r.introduction && <section className="col"><h3>{t("introduction")}</h3><p dir="auto" style={{ margin: 0 }}>{r.introduction}</p></section>}
          {(r.themes ?? []).map((th: R, i: number) => <section key={i} className="col"><h3 dir="auto">{th.heading}</h3>
            <p dir="auto" style={{ margin: 0, whiteSpace: "pre-wrap" }}>{th.text}</p>{S(th.sources)}</section>)}
          {r.gaps && <section className="col"><h3>{t("gaps")}</h3><p dir="auto" style={{ margin: 0 }}>{r.gaps}</p></section>}
          {r.conclusion && <section className="col"><h3>{t("conclusion")}</h3><p dir="auto" style={{ margin: 0 }}>{r.conclusion}</p></section>}
          <section className="col"><h3>{t("sourcesReviewed")}</h3>{(r.references ?? []).map((x: R) => <span key={x.n} dir="auto">[{x.n}] {x.document}</span>)}</section>
        </>}

        {kind === "slides" && (r.slides ?? []).map((s: R, i: number) => (
          <div key={i} className="slide-card"><span className="muted small">{t("slideN", { n: i + 1 })}</span>
            <strong dir="auto" style={{ fontSize: 16 }}>{s.title}</strong>
            <ul>{s.bullets.map((b: string, j: number) => <li key={j} dir="auto">{b}</li>)}</ul>
            {s.notes && <span className="small muted" dir="auto"><em>{t("speakerNotes")}: {s.notes}</em></span>}{S(s.sources)}
          </div>))}
      </div>
    </div>
  );
}
