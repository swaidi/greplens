import { useEffect, useRef, useState } from "react";
import { Answer, api, ChatRow, Doc, Finding, Note, Profile, Review, Segment, Workspace } from "../api";
import { Key, useI18n } from "../i18n";
import { IconDoc } from "../icons";
import { CategoryPicker } from "./Home";

type Props = { doc: Doc | null; workspace: Workspace | null; segments: Segment[]; notes: Note[]; activeNote: string | null;
  onJump: (segmentId: string | null, page?: number) => void; reloadNotes: () => void; prefill: { text: string; ts: number } | null };

export default function AssistantPanel(props: Props) {
  const { t } = useI18n();
  const [tab, setTab] = useState<"ask" | "review">("ask");
  const [cats, setCats] = useState<string[]>([]);
  useEffect(() => { if (props.workspace) setCats(props.workspace.categories ?? []); }, [props.workspace?.id]); // eslint-disable-line
  useEffect(() => { if (props.prefill) setTab("ask"); }, [props.prefill]);
  return (
    <aside className="assist-pane" aria-label="Assistant">
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "ask"} className={tab === "ask" ? "on" : ""} onClick={() => setTab("ask")}>{t("ask")}</button>
        <button role="tab" aria-selected={tab === "review"} className={tab === "review" ? "on" : ""} onClick={() => setTab("review")}>{t("review")}</button>
      </div>
      {props.doc?.status !== "ready" ? <div className="muted small" style={{ padding: 16 }}>{props.doc ? t("preparing") : t("noDocuments")}</div>
        : tab === "ask" ? <AskTab {...props} cats={cats} setCats={setCats} /> : <ReviewTab {...props} cats={cats} setCats={setCats} />}
    </aside>
  );
}

// Questions keep running when you switch documents or tabs; the answer is shown when you come back.
const inflight = new Map<string, Promise<unknown>>();
const errors = new Map<string, string>();

type TabProps = Props & { cats: string[]; setCats: (c: string[]) => void };

function useProfiles() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  useEffect(() => { api.get<Profile[]>("/api/profiles").then(setProfiles); }, []);
  return profiles;
}

function EngineError({ message }: { message: string }) {
  const { t } = useI18n();
  return <div className="error-box small">{message}{/model|engine/i.test(message) ? ` ${t("engineMissing")}` : ""}</div>;
}

function AskTab({ doc, cats, setCats, onJump, reloadNotes, notes, activeNote, prefill }: TabProps) {
  const { t, lang } = useI18n();
  const profiles = useProfiles();
  const [mode, setMode] = useState<"category" | "doc">("category");
  const [rows, setRows] = useState<ChatRow[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pickCats, setPickCats] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const docRef = useRef(doc?.id);
  docRef.current = doc?.id;
  const loadChat = (id: string) => api.get<ChatRow[]>(`/api/documents/${id}/chat`).then((r) => { if (docRef.current === id) setRows(r); });
  useEffect(() => {
    if (!doc) return;
    const id = doc.id;
    loadChat(id);
    setError(errors.get(id) ?? "");
    const running = inflight.get(id);
    setBusy(!!running);
    if (running) running.finally(() => { if (docRef.current === id) { setBusy(false); setError(errors.get(id) ?? ""); loadChat(id); } });
  }, [doc?.id]); // eslint-disable-line
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [rows, busy]);
  useEffect(() => {
    if (prefill) { setQuestion(`“${prefill.text.slice(0, 300)}”\n`); setTimeout(() => inputRef.current?.focus(), 50); }
  }, [prefill]);

  const catNames = cats.map((c) => { const p = profiles.find((x) => x.id === c); return p ? (lang === "ar" ? p.name_ar : p.name) : c; }).join(" + ");
  const send = async () => {
    if (!doc || !question.trim() || busy) return;
    const q = question.trim();
    const id = doc.id;
    setQuestion(""); setBusy(true); setError(""); errors.delete(id);
    if (inputRef.current) inputRef.current.style.height = "";
    setRows((r) => [...r, { id: `tmp-${Date.now()}`, role: "user", content: q, meta: {} }]);
    const request = api.post<Answer>(`/api/documents/${id}/ask`, { question: q, mode, categories: cats })
      .catch((e) => { errors.set(id, String(e).replace(/^Error: /, "")); })
      .finally(() => inflight.delete(id));
    inflight.set(id, request);
    await request;
    if (docRef.current === id) { setBusy(false); setError(errors.get(id) ?? ""); loadChat(id); }
  };
  const saveAsNote = async (a: Answer) => {
    if (!doc) return;
    const c = a.citations[0];
    const body = [a.from_document || t("notFound"), a.general_knowledge ? `${t("genKnow")}: ${a.general_knowledge}` : ""].filter(Boolean).join("\n\n");
    await api.post("/api/notes", { document_id: doc.id, body, category: "query", ai_answer: true, segment_id: c?.segment_id ?? null,
      page: c?.page ?? null, location: c?.location ?? null, quote: "" });
    reloadNotes();
  };
  const linkToHighlight = async (a: Answer) => {
    const n = notes.find((x) => x.id === activeNote);
    if (!n) return;
    await api.patch(`/api/notes/${n.id}`, { body: [n.body, `${t("aiAnswer")}: ${a.from_document}`].filter(Boolean).join("\n\n") });
    reloadNotes();
  };

  return (
    <div className="col" style={{ flex: "1 1 auto", minHeight: 0, gap: 0 }}>
      <div className="col" style={{ padding: "12px 14px", borderBottom: "1px solid var(--line2)" }}>
        <div className="seg" role="group">
          <button className={mode === "category" ? "on" : ""} onClick={() => setMode("category")}>{t("catMode")}</button>
          <button className={mode === "doc" ? "on" : ""} onClick={() => setMode("doc")}>{t("docOnly")}</button>
        </div>
        <div className="row small muted" style={{ justifyContent: "space-between" }}>
          <span>{mode === "category" ? t("catHint", { cats: catNames || "General" }) : t("docHint")}</span>
          {mode === "category" && <button className="btn small" onClick={() => setPickCats(!pickCats)}>{t("categories")}</button>}
        </div>
        {pickCats && mode === "category" && <CategoryPicker value={cats} onChange={setCats} />}
      </div>
      <div className="chat">
        {rows.map((r) => r.role === "user"
          ? <div key={r.id} className="bubble" dir="auto">{r.content}</div>
          : <AnswerCard key={r.id} a={r.meta as Answer} onJump={onJump} onSave={saveAsNote}
              onLink={activeNote ? linkToHighlight : undefined} />)}
        {busy && <div className="muted small">{t("thinking")}</div>}
        {error && <EngineError message={error} />}
        <div ref={endRef} />
      </div>
      <div className="row" style={{ padding: "12px 14px", borderTop: "1px solid var(--line)", alignItems: "flex-end" }}>
        <label className="grow"><span className="sr">{t("askPlaceholder")}</span>
          <textarea ref={inputRef} className="input ask-input" rows={2} dir="auto" placeholder={t("askPlaceholder")} value={question}
            onChange={(e) => {
              setQuestion(e.target.value);
              const el = e.target;  // grow with the text, up to about half the panel
              el.style.height = "auto";
              el.style.height = `${Math.min(el.scrollHeight + 2, Math.max(160, window.innerHeight * 0.45))}px`;
            }}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} /></label>
        <button className="btn big primary" disabled={busy || !question.trim()} onClick={send}>{t("send")}</button>
      </div>
      <div className="muted small" style={{ padding: "0 14px 6px" }}>{t("enterHint")}</div>
      {rows.length > 0 && (
        <button className="btn small" style={{ margin: "0 14px 10px", alignSelf: "flex-start" }}
          onClick={async () => { if (doc) { await api.del(`/api/documents/${doc.id}/chat`); setRows([]); } }}>{t("clearChat")}</button>
      )}
    </div>
  );
}

function AnswerCard({ a, onJump, onSave, onLink }: { a: Answer; onJump: Props["onJump"]; onSave: (a: Answer) => void; onLink?: (a: Answer) => void }) {
  const { t } = useI18n();
  if (!a || a.found === undefined) return null;
  return (
    <div className="answer">
      {a.found ? (
        <div>
          <div className="label"><IconDoc size={13} />{t("fromDoc")}</div>
          <div dir="auto" style={{ whiteSpace: "pre-wrap" }}>{a.from_document}</div>
          <div className="row wrap" style={{ marginTop: 8, gap: 6 }}>
            {a.citations.map((c) => <button key={c.n} className="cite" dir="auto" onClick={() => onJump(c.segment_id, c.page)}>{c.location} →</button>)}
          </div>
        </div>
      ) : (
        <div><strong>{t("notFound")}</strong><div className="muted small">{t("searchedPages", { n: a.searched_pages })}</div></div>
      )}
      {a.general_knowledge && (
        <div className="gk"><div className="label">{t("genKnow")} · {a.categories.join(" + ")}</div>
          <div dir="auto" style={{ whiteSpace: "pre-wrap" }}>{a.general_knowledge}</div></div>
      )}
      <div className="actions">
        <button className="btn small" onClick={() => onSave(a)}>{t("saveAsNote")}</button>
        {onLink && a.found && <button className="btn small" onClick={() => onLink(a)}>{t("linkToHighlight")}</button>}
        <button className="btn small" onClick={() => navigator.clipboard?.writeText([a.from_document, a.general_knowledge].filter(Boolean).join("\n\n"))}>{t("copy")}</button>
      </div>
    </div>
  );
}

function ReviewTab({ doc, cats, setCats, onJump, reloadNotes }: TabProps) {
  const { t } = useI18n();
  const [mode, setMode] = useState<"open" | "checklist" | "instruction">("open");
  const [checklist, setChecklist] = useState("");
  const [instruction, setInstruction] = useState("");
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<Record<string, string>>(() => {
    try { return JSON.parse(localStorage.getItem("greplens.checklists") ?? "{}"); } catch { return {}; }
  });
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (doc) api.get<Review>(`/api/documents/${doc.id}/reviews/latest`).then((r) => setReview(r?.id ? r : null)); }, [doc?.id]); // eslint-disable-line
  useEffect(() => {
    if (!review || !["running", "queued", "pausing"].includes(review.status)) return;
    const timer = setInterval(() => api.get<Review>(`/api/reviews/${review.id}`).then(setReview), 2000);
    return () => clearInterval(timer);
  }, [review?.id, review?.status]); // eslint-disable-line

  const run = async () => {
    if (!doc) return;
    setError("");
    try {
      const r = await api.post<Review>(`/api/documents/${doc.id}/reviews`, {
        categories: cats, mode, instruction, checklist: checklist.split("\n").map((x) => x.trim()).filter(Boolean) });
      setReview({ ...r, findings: [] });
    } catch (e) { setError(String(e)); }
  };
  const saveChecklist = () => {
    const first = checklist.trim().split("\n")[0].slice(0, 40);
    const name = `${first}${first.length >= 40 ? "…" : ""} (${checklist.trim().split("\n").length})`;
    const next = { ...saved, [name]: checklist };
    setSaved(next);
    try { localStorage.setItem("greplens.checklists", JSON.stringify(next)); } catch { /* storage unavailable */ }
  };
  const saveFinding = async (f: Finding) => {
    if (!doc) return;
    await api.post("/api/notes", { document_id: doc.id, quote: f.quote, segment_id: f.segment_id, page: f.page, location: f.location,
      category: f.severity === "high" ? "risk" : "query", ai_answer: true, body: `${f.title}\n${t("recommendation")}: ${f.recommendation}` });
    reloadNotes();
  };
  const running = review && ["running", "queued", "pausing"].includes(review.status);
  const modes: { id: typeof mode; label: Key; desc: Key }[] = [
    { id: "open", label: "openEnded", desc: "openDesc" }, { id: "checklist", label: "checklist", desc: "checklistDesc" },
    { id: "instruction", label: "instruction", desc: "instructionDesc" }];

  return (
    <div className="col" style={{ flex: "1 1 auto", minHeight: 0, overflow: "auto", padding: 14, gap: 16 }}>
      <div className="col">
        <div className="row" style={{ justifyContent: "space-between" }}><h4 style={{ fontSize: 13 }}>{t("categories")}</h4>
          <span className="muted small">{t("selectUpTo2")}</span></div>
        <CategoryPicker value={cats} onChange={setCats} />
      </div>
      <div className="col">
        <h4 style={{ fontSize: 13 }}>{t("reviewType")}</h4>
        {modes.map((m) => (
          <button key={m.id} className={`mode-card ${mode === m.id ? "on" : ""}`} aria-pressed={mode === m.id} onClick={() => setMode(m.id)}>
            <span className="radio" /><span className="col" style={{ gap: 2 }}><strong>{t(m.label)}</strong><span className="muted small">{t(m.desc)}</span></span>
          </button>
        ))}
        {mode === "checklist" && (
          <div className="col card" style={{ background: "var(--soft2)" }}>
            <textarea className="input" rows={5} dir="auto" placeholder={t("checklistPlaceholder")} value={checklist}
              onChange={(e) => setChecklist(e.target.value)} />
            <input ref={fileRef} type="file" accept=".txt,.md,.csv" hidden onChange={async (e) => {
              const f = e.target.files?.[0]; if (f) setChecklist((await f.text()).trim()); e.target.value = ""; }} />
            <div className="row wrap">
              <button className="btn small" onClick={() => fileRef.current?.click()}>{t("uploadChecklist")}</button>
              <button className="btn small" disabled={!checklist.trim()} onClick={saveChecklist}>{t("saveChecklist")}</button>
              {Object.keys(saved).length > 0 && (
                <select className="input" style={{ minHeight: 30, width: "auto" }} aria-label={t("savedChecklists")} value=""
                  onChange={(e) => e.target.value && setChecklist(saved[e.target.value])}>
                  <option value="">{t("savedChecklists")}</option>
                  {Object.keys(saved).map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
              )}
            </div>
          </div>
        )}
        {mode === "instruction" && (
          <label className="col"><span className="muted small">{t("instructionLabel")}</span>
            <textarea className="input" rows={3} dir="auto" value={instruction} onChange={(e) => setInstruction(e.target.value)} /></label>
        )}
      </div>
      <div className="col">
        <button className="btn big primary" disabled={!!running || (mode === "checklist" && !checklist.trim()) || (mode === "instruction" && !instruction.trim())}
          onClick={run}>{t("runReview")}</button>
        <span className="muted small" style={{ textAlign: "center" }}>{t("estimate")}</span>
      </div>
      {error && <EngineError message={error} />}
      {review && (
        <div className="col" style={{ gap: 10 }}>
          <div className="card col" style={{ padding: "10px 12px" }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>{review.status === "done" ? t("reviewDone") : review.status === "paused" ? t("reviewPaused") :
                review.status === "error" ? t("error") : t("reviewing", { p: Math.min(review.position + 1, review.total || 1), t: review.total || "…" })}</strong>
              {running && <button className="btn small" onClick={() => api.post(`/api/reviews/${review.id}/pause`).then(() => setReview({ ...review, status: "pausing" }))}>{t("pause")}</button>}
              {(review.status === "paused" || review.status === "error") && <button className="btn small"
                onClick={() => api.post(`/api/reviews/${review.id}/resume`).then(() => setReview({ ...review, status: "running" }))}>{t("resume")}</button>}
            </div>
            <div className="bar"><div style={{ width: `${review.total ? (review.position / review.total) * 100 : 3}%` }} /></div>
            {review.status === "error" && <span className="small" style={{ color: "var(--danger-text)" }}>{review.message}</span>}
          </div>
          <h4 style={{ fontSize: 13 }}>{t("findings")} · {review.findings?.length ?? 0}</h4>
          {review.status === "done" && !review.findings?.length && <span className="muted small">{t("noFindings")}</span>}
          {review.findings?.map((f) => (
            <div key={f.id} className="card col" style={{ padding: 12, gap: 7 }}>
              <div className="row wrap" style={{ gap: 6 }}>
                <span className={`pill ${f.severity}`}>{t(f.severity as Key)}</span><span className="pill">{f.category}</span>
              </div>
              <strong dir="auto">{f.title}</strong>
              <div className="small" dir="auto"><strong>{t("location")}:</strong> {f.location}</div>
              {f.quote && <div className="quote" dir="auto">“{f.quote}”</div>}
              {f.recommendation && <div className="small" dir="auto"><strong>{t("recommendation")}:</strong> {f.recommendation}</div>}
              <div className="row" style={{ gap: 6 }}>
                <button className="btn soft small" onClick={() => onJump(f.segment_id, f.page)}>{t("jump")}</button>
                <button className="btn small" onClick={() => saveFinding(f)}>{t("saveAsNote")}</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
