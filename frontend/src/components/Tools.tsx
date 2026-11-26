import { useEffect, useRef, useState } from "react";
import { api, Doc, download } from "../api";
import { Key, useI18n } from "../i18n";
import { notesApi } from "../undo";

export type Job = {
  id: string; kind: string; status: string; progress: number; message: string; file: string | null;
  params: Record<string, unknown>; result: Record<string, unknown> | null;
};
type Scope = { scope: "all" | "page" | "range"; start: number; end: number };
export type MindNode = { title: string; page?: number; segment_id?: string; children?: MindNode[] };
export type Question = { question: string; options: string[]; answer: number; explanation: string; location: string; segment_id: string; page: number };
type Detail = { type: string; value: string; count: number; locations: string[]; segment_ids: string[] };

const RUNNING = ["queued", "running", "cancelling"];

/** Start a background task for this document and follow its progress. */
function useJob(doc: Doc, kind: string) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setJob(null); setError("");
    api.get<Record<string, Job>>(`/api/documents/${doc.id}/jobs`).then((all) => setJob(all[kind] ?? null)).catch(() => {});
  }, [doc.id, kind]);
  useEffect(() => {
    if (!job || !RUNNING.includes(job.status)) return;
    const timer = setInterval(() => api.get<Job>(`/api/jobs/${job.id}`).then(setJob), 1200);
    return () => clearInterval(timer);
  }, [job?.id, job?.status]); // eslint-disable-line
  const run = async (params: Record<string, unknown>) => {
    setError("");
    try { setJob(await api.post<Job>(`/api/documents/${doc.id}/jobs`, { kind, params })); }
    catch (e) { setError(String(e).replace(/^Error: /, "")); }
  };
  const cancel = () => job && api.post(`/api/jobs/${job.id}/cancel`).then(() => setJob({ ...job, status: "cancelling" }));
  return { job, run, cancel, error, running: !!job && RUNNING.includes(job.status) };
}

function Progress({ job, onCancel }: { job: Job; onCancel: () => void }) {
  const { t } = useI18n();
  return (
    <div className="col" style={{ gap: 4 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="small">{job.message || t("working")}</span>
        <button className="btn small" onClick={onCancel}>{t("stop")}</button>
      </div>
      <div className="bar"><div style={{ width: `${Math.max(3, job.progress * 100)}%` }} /></div>
    </div>
  );
}

function Status({ job, error }: { job: Job | null; error: string }) {
  const { t } = useI18n();
  if (error) return <div className="error-box small">{error}</div>;
  if (job?.status === "error") return <div className="error-box small">{job.message}</div>;
  if (job?.status === "cancelled") return <span className="muted small">{t("stopped")}</span>;
  return null;
}

function ScopePicker({ value, onChange, page, pages }: { value: Scope; onChange: (s: Scope) => void; page: number; pages: number }) {
  const { t } = useI18n();
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="seg" role="group" aria-label={t("scope")}>
        {(["all", "page", "range"] as const).map((s) => (
          <button key={s} className={value.scope === s ? "on" : ""}
            onClick={() => onChange({ scope: s, start: s === "page" ? page : value.start || 1, end: value.end || pages })}>
            {t(s === "all" ? "scopeAll" : s === "page" ? "scopePage" : "scopeRange")}</button>
        ))}
      </div>
      {value.scope === "page" && <span className="muted small">{t("page")} {page}</span>}
      {value.scope === "range" && (
        <div className="row small">
          <label className="row">{t("from")} <input className="input" type="number" min={1} max={pages} style={{ width: 80, minHeight: 34 }}
            value={value.start} onChange={(e) => onChange({ ...value, start: Number(e.target.value) })} /></label>
          <label className="row">{t("to")} <input className="input" type="number" min={1} max={pages} style={{ width: 80, minHeight: 34 }}
            value={value.end} onChange={(e) => onChange({ ...value, end: Number(e.target.value) })} /></label>
        </div>
      )}
    </div>
  );
}

function Section({ title, hint, open, onToggle, children }: { title: string; hint: string; open: boolean; onToggle: () => void; children: React.ReactNode }) {
  return (
    <div className="card col" style={{ padding: 0, gap: 0 }}>
      <button className="tool-head" aria-expanded={open} onClick={onToggle}>
        <span className="col" style={{ gap: 0, textAlign: "start" }}><strong>{title}</strong><span className="muted small">{hint}</span></span>
        <span aria-hidden="true">{open ? "−" : "+"}</span>
      </button>
      {open && <div className="col" style={{ padding: "0 14px 14px", gap: 10 }}>{children}</div>}
    </div>
  );
}

type ToolsProps = {
  doc: Doc; page: number; onJump: (segmentId: string | null, page?: number) => void; reloadNotes: () => void;
  sectionRequest: { segment_ids: string[]; title: string; ts: number } | null;
  onOpenMindMap: (m: MindNode) => void; onOpenQuiz: (q: Question[]) => void; redactCount: number;
};

export default function ToolsTab(props: ToolsProps) {
  const { t } = useI18n();
  const { doc } = props;
  const [open, setOpen] = useState<string>("summary");
  const pdf = doc.kind === "pdf";
  const toggle = (k: string) => setOpen(open === k ? "" : k);
  useEffect(() => { if (props.sectionRequest) setOpen("summary"); }, [props.sectionRequest]);
  return (
    <div className="col" style={{ flex: "1 1 auto", minHeight: 0, overflow: "auto", padding: 14, gap: 10 }}>
      <Section title={t("toolSummary")} hint={t("toolSummaryHint")} open={open === "summary"} onToggle={() => toggle("summary")}>
        <SummaryTool {...props} /></Section>
      <Section title={t("toolTranslate")} hint={t("toolTranslateHint")} open={open === "translate"} onToggle={() => toggle("translate")}>
        <TranslateTool {...props} /></Section>
      <Section title={t("toolContract")} hint={t("toolContractHint")} open={open === "contract"} onToggle={() => toggle("contract")}>
        <ContractTool {...props} /></Section>
      <Section title={t("toolTables")} hint={t("toolTablesHint")} open={open === "tables"} onToggle={() => toggle("tables")}>
        {doc.kind === "pdf" || doc.kind === "docx" ? <TablesTool {...props} /> : <span className="muted small">{t("tablesNotHere")}</span>}</Section>
      <Section title={t("toolStudy")} hint={t("toolStudyHint")} open={open === "study"} onToggle={() => toggle("study")}>
        <StudyTool {...props} /></Section>
      <Section title={t("toolRedact")} hint={t("toolRedactHint")} open={open === "redact"} onToggle={() => toggle("redact")}>
        {pdf ? <RedactTool {...props} /> : <span className="muted small">{t("pdfOnly")}</span>}</Section>
      <Section title={t("toolPdfNotes")} hint={t("toolPdfNotesHint")} open={open === "pdfnotes"} onToggle={() => toggle("pdfnotes")}>
        {pdf ? <PdfNotesTool {...props} /> : <span className="muted small">{t("pdfNotesOther")}</span>}</Section>
    </div>
  );
}

function SummaryTool({ doc, page, reloadNotes, sectionRequest }: ToolsProps) {
  const { t } = useI18n();
  const { job, run, cancel, error, running } = useJob(doc, "summary");
  const [scope, setScope] = useState<Scope>({ scope: "all", start: 1, end: doc.pages || 1 });
  const [length, setLength] = useState("brief");
  const [lang, setLang] = useState("auto");
  const last = useRef(0);
  useEffect(() => {
    if (sectionRequest && sectionRequest.ts !== last.current) {
      last.current = sectionRequest.ts;
      run({ segment_ids: sectionRequest.segment_ids, section_title: sectionRequest.title, length, language: lang });
    }
  }, [sectionRequest]); // eslint-disable-line
  const r = job?.status === "done" ? job.result as { text: string; pages: string; section: string } : null;
  return (
    <>
      <ScopePicker value={scope} onChange={setScope} page={page} pages={doc.pages || 1} />
      <div className="row wrap">
        <div className="seg">{(["brief", "detailed"] as const).map((l) => (
          <button key={l} className={length === l ? "on" : ""} onClick={() => setLength(l)}>{t(l === "brief" ? "brief" : "detailed")}</button>))}</div>
        <LangPick value={lang} onChange={setLang} />
      </div>
      {!running && <button className="btn primary" onClick={() => run({ ...scope, length, language: lang })}>{t("summarize")}</button>}
      {running && job && <Progress job={job} onCancel={cancel} />}
      <Status job={job} error={error} />
      {r && (
        <div className="answer"><div>
          <div className="label">{r.section ? `${t("sectionSummary")} · ${r.section}` : t("summaryOf")} · {r.pages}</div>
          <div dir="auto" style={{ whiteSpace: "pre-wrap" }}>{r.text}</div>
        </div>
          <div className="actions">
            <button className="btn small" onClick={async () => {
              await notesApi.create({ document_id: doc.id, body: `${t("summaryOf")} (${r.pages})\n\n${r.text}`, category: "info",
                ai_answer: 1, page: null, location: r.section || r.pages, quote: "" });
              reloadNotes();
            }}>{t("saveAsNote")}</button>
            <button className="btn small" onClick={() => navigator.clipboard?.writeText(r.text)}>{t("copy")}</button>
          </div>
        </div>
      )}
    </>
  );
}

function LangPick({ value, onChange, withAuto = true }: { value: string; onChange: (v: string) => void; withAuto?: boolean }) {
  const { t } = useI18n();
  return (
    <select className="input" style={{ width: "auto", minHeight: 34 }} value={value} aria-label={t("language")} onChange={(e) => onChange(e.target.value)}>
      {withAuto && <option value="auto">{t("langAuto")}</option>}
      <option value="en">English</option><option value="ar">العربية</option>
    </select>
  );
}

function FileResult({ job, label }: { job: Job | null; label: Key }) {
  const { t } = useI18n();
  if (job?.status !== "done") return null;
  return (
    <div className="row wrap" style={{ justifyContent: "space-between" }}>
      <span className="small" style={{ color: "var(--ok-text)" }}>{t("ready")}</span>
      <button className="btn soft small" onClick={() => download(`/api/jobs/${job.id}/file`)}>{t(label)}</button>
    </div>
  );
}

function TranslateTool({ doc, page }: ToolsProps) {
  const { t } = useI18n();
  const { job, run, cancel, error, running } = useJob(doc, "translate");
  const [scope, setScope] = useState<Scope>({ scope: "all", start: 1, end: doc.pages || 1 });
  const [target, setTarget] = useState("");
  return (
    <>
      <ScopePicker value={scope} onChange={setScope} page={page} pages={doc.pages || 1} />
      <label className="row small">{t("translateTo")}
        <select className="input" style={{ width: "auto", minHeight: 34 }} value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">{t("langOther")}</option><option value="ar">العربية</option><option value="en">English</option>
        </select></label>
      <span className="muted small">{t("translateNote")}</span>
      {!running && <button className="btn primary" onClick={() => run({ ...scope, target: target || undefined })}>{t("translateDoc")}</button>}
      {running && job && <Progress job={job} onCancel={cancel} />}
      <Status job={job} error={error} />
      <FileResult job={job} label="downloadWord" />
    </>
  );
}

function TablesTool({ doc, page }: ToolsProps) {
  const { t } = useI18n();
  const { job, run, cancel, error, running } = useJob(doc, "tables");
  const [scope, setScope] = useState<Scope>({ scope: "all", start: 1, end: doc.pages || 1 });
  return (
    <>
      {doc.kind === "pdf" && <ScopePicker value={scope} onChange={setScope} page={page} pages={doc.pages || 1} />}
      {!running && <button className="btn primary" onClick={() => run(scope)}>{t("findTables")}</button>}
      {running && job && <Progress job={job} onCancel={cancel} />}
      <Status job={job} error={error} />
      {job?.status === "done" && <span className="small">{t("tablesFound", { n: String((job.result as { tables: number }).tables) })}</span>}
      <FileResult job={job} label="downloadExcel" />
    </>
  );
}

function StudyTool({ doc, page, onOpenMindMap, onOpenQuiz }: ToolsProps) {
  const { t } = useI18n();
  const mm = useJob(doc, "mindmap");
  const qz = useJob(doc, "quiz");
  const [scope, setScope] = useState<Scope>({ scope: "all", start: 1, end: doc.pages || 1 });
  const [count, setCount] = useState(8);
  const [lang, setLang] = useState("auto");
  return (
    <>
      <ScopePicker value={scope} onChange={setScope} page={page} pages={doc.pages || 1} />
      <LangPick value={lang} onChange={setLang} />
      <div className="col" style={{ gap: 6 }}>
        <strong>{t("mindMap")}</strong>
        {!mm.running && <div className="row wrap">
          <button className="btn primary" onClick={() => mm.run({ ...scope, language: lang })}>{t("makeMindMap")}</button>
          {mm.job?.status === "done" && <button className="btn soft" onClick={() => onOpenMindMap(mm.job!.result as MindNode)}>{t("viewMap")}</button>}
        </div>}
        {mm.running && mm.job && <Progress job={mm.job} onCancel={mm.cancel} />}
        <Status job={mm.job} error={mm.error} />
      </div>
      <div className="col" style={{ gap: 6 }}>
        <strong>{t("quiz")}</strong>
        <label className="row small">{t("questions")}
          <select className="input" style={{ width: "auto", minHeight: 34 }} value={count} onChange={(e) => setCount(Number(e.target.value))}>
            {[5, 8, 12, 20].map((n) => <option key={n} value={n}>{n}</option>)}
          </select></label>
        {!qz.running && <div className="row wrap">
          <button className="btn primary" onClick={() => qz.run({ ...scope, count, language: lang })}>{t("makeQuiz")}</button>
          {qz.job?.status === "done" && <button className="btn soft" onClick={() => onOpenQuiz((qz.job!.result as { questions: Question[] }).questions)}>{t("startQuiz")}</button>}
        </div>}
        {qz.running && qz.job && <Progress job={qz.job} onCancel={qz.cancel} />}
        <Status job={qz.job} error={qz.error} />
      </div>
    </>
  );
}

function ContractTool({ doc, onJump }: ToolsProps) {
  const { t } = useI18n();
  const { job, run, cancel, error, running } = useJob(doc, "contract_details");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [draftError, setDraftError] = useState("");
  const alertBox = (m: string) => setDraftError(m.replace(/^\{"detail":"|"\}$/g, ""));
  const items = job?.status === "done" ? (job.result as { items: Detail[] }).items : [];
  const draft = async () => {
    setBusy(true);
    const res = await fetch(`/api/documents/${doc.id}/contract-draft`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ replacements: items.map((i) => ({ old: i.value, new: values[i.value] ?? "" })) }) });
    if (!res.ok) { setBusy(false); alertBox(await res.text()); return; }
    const blob = await res.blob();
    const name = decodeURIComponent((res.headers.get("Content-Disposition") ?? "").split("''")[1] ?? "new draft.docx");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; a.click(); URL.revokeObjectURL(a.href);
    setBusy(false);
  };
  return (
    <>
      {!running && <button className="btn primary" onClick={() => run({})}>{items.length ? t("findAgain") : t("findDetails")}</button>}
      {running && job && <Progress job={job} onCancel={cancel} />}
      <Status job={job} error={error} />
      {items.length > 0 && (
        <>
          <span className="muted small">{t("detailsHint")}</span>
          <div className="col" style={{ gap: 6, maxHeight: 420, overflow: "auto" }}>
            {items.map((i) => (
              <div key={i.type + i.value} className="detail-row">
                <div className="row wrap" style={{ gap: 6 }}>
                  <span className={`pill ${i.type === "placeholder" ? "warn" : ""}`}>{t(`dt_${i.type}` as Key) || i.type}</span>
                  <strong dir="auto" style={{ wordBreak: "break-word" }}>{i.value}</strong>
                  <span className="muted small">×{i.count}</span>
                </div>
                <div className="row wrap small" style={{ gap: 4 }}>
                  {i.locations.slice(0, 3).map((loc, k) => (
                    <button key={k} className="cite" onClick={() => onJump(i.segment_ids[k], undefined)}>{loc} →</button>))}
                </div>
                <input className="input" dir="auto" placeholder={t("newValue")} value={values[i.value] ?? ""}
                  onChange={(e) => setValues({ ...values, [i.value]: e.target.value })} style={{ minHeight: 34 }} />
              </div>
            ))}
          </div>
          <button className="btn primary" disabled={busy} onClick={draft}>{t("makeDraft")}</button>
          {draftError && <div className="error-box small">{draftError}</div>}
          <span className="muted small">{t("draftNote")}</span>
        </>
      )}
    </>
  );
}

function RedactTool({ doc, reloadNotes, redactCount }: ToolsProps) {
  const { t } = useI18n();
  const [term, setTerm] = useState("");
  const [msg, setMsg] = useState("");
  return (
    <>
      <span className="muted small">{t("redactHint")}</span>
      <div className="row">
        <input className="input grow" dir="auto" placeholder={t("redactTerm")} value={term} onChange={(e) => setTerm(e.target.value)}
          style={{ minHeight: 36 }} />
        <button className="btn" disabled={!term.trim()} onClick={async () => {
          const r = await api.post<{ added: number }>(`/api/documents/${doc.id}/redact/search`, { term });
          setMsg(t("redactAdded", { n: String(r.added) })); reloadNotes();
        }}>{t("markAll")}</button>
      </div>
      {msg && <span className="small">{msg}</span>}
      <span className="small">{t("redactMarked", { n: String(redactCount) })}</span>
      <div className="row wrap">
        <button className="btn primary" disabled={!redactCount} onClick={() => download(`/api/documents/${doc.id}/redacted.pdf`)}>{t("makeRedacted")}</button>
        <button className="btn" disabled={!redactCount} onClick={async () => { await api.del(`/api/documents/${doc.id}/redact`); reloadNotes(); setMsg(""); }}>{t("clearMarks")}</button>
      </div>
      <span className="muted small">{t("redactNote")}</span>
    </>
  );
}

function PdfNotesTool({ doc }: ToolsProps) {
  const { t } = useI18n();
  const [findings, setFindings] = useState(true);
  return (
    <>
      <label className="row small"><input type="checkbox" checked={findings} onChange={(e) => setFindings(e.target.checked)} /> {t("includeFindings")}</label>
      <button className="btn primary" onClick={() => download(`/api/documents/${doc.id}/annotated.pdf?findings=${findings}`)}>{t("downloadPdfNotes")}</button>
      <span className="muted small">{t("pdfNotesNote")}</span>
    </>
  );
}

/** Mind map: a collapsible tree; click a branch to open that place in the document. */
export function MindMapView({ map, onJump, onClose }: { map: MindNode; onJump: (s: string | null, p?: number) => void; onClose: () => void }) {
  const { t } = useI18n();
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 900, maxHeight: "85vh", overflow: "auto" }} role="dialog" aria-label={t("mindMap")}
        onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ fontSize: 19 }}>{t("mindMap")}</h2>
          <button className="btn small" onClick={onClose}>{t("close")}</button></div>
        <div className="mm-root">{map.title}</div>
        <div className="mm-branches">
          {(map.children ?? []).map((c, i) => (
            <div key={i} className="mm-branch">
              <button className="mm-node" onClick={() => { onJump(c.segment_id ?? null, c.page); onClose(); }} dir="auto">
                {c.title}{c.page ? <span className="muted small"> · p.{c.page}</span> : null}</button>
              <ul>{(c.children ?? []).map((g, j) => <li key={j} dir="auto">{g.title}</li>)}</ul>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export function QuizView({ questions, onJump, onClose }: { questions: Question[]; onJump: (s: string | null, p?: number) => void; onClose: () => void }) {
  const { t } = useI18n();
  const [i, setI] = useState(0);
  const [picked, setPicked] = useState<number | null>(null);
  const [score, setScore] = useState(0);
  const q = questions[i];
  const done = i >= questions.length;
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 640 }} role="dialog" aria-label={t("quiz")} onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ fontSize: 19 }}>{t("quiz")}</h2>
          <span className="muted small">{done ? "" : `${i + 1} / ${questions.length}`}</span>
        </div>
        {done ? (
          <>
            <strong style={{ fontSize: 18 }}>{t("quizScore", { s: String(score), n: String(questions.length) })}</strong>
            <div className="row"><button className="btn" onClick={() => { setI(0); setScore(0); setPicked(null); }}>{t("again")}</button>
              <button className="btn primary" onClick={onClose}>{t("close")}</button></div>
          </>
        ) : (
          <>
            <strong dir="auto">{q.question}</strong>
            <div className="col">
              {q.options.map((o, k) => {
                const state = picked === null ? "" : k === q.answer ? "right" : k === picked ? "wrong" : "";
                return <button key={k} className={`quiz-opt ${state}`} disabled={picked !== null} dir="auto"
                  onClick={() => { setPicked(k); if (k === q.answer) setScore(score + 1); }}>{o}</button>;
              })}
            </div>
            {picked !== null && (
              <div className="col">
                <span className="small" dir="auto">{q.explanation}</span>
                <div className="row wrap">
                  <button className="cite" onClick={() => { onJump(q.segment_id, q.page); onClose(); }}>{q.location} →</button>
                  <span className="grow" />
                  <button className="btn primary" onClick={() => { setI(i + 1); setPicked(null); }}>{i + 1 < questions.length ? t("next") : t("finishQuiz")}</button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
