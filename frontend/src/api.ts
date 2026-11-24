export type Workspace = {
  id: string; name: string; categories: string[]; created: number; updated: number;
  documents?: number | Doc[]; notes?: number; findings?: number; authors?: number;
};
export type Doc = {
  id: string; workspace_id: string; filename: string; kind: string; pages: number; ocr_pages: number;
  status: string; progress: number; message: string; revision_of?: string | null;
};
export type Segment = {
  id: string; seq: number; page: number; approx_page: number; article: string; clause: string;
  paragraph: number; location: string; text: string; bbox: number[];
};
export type InkStroke = { color: string; width: number; points: number[][] };
export type Note = {
  id: string; document_id: string; mark: "highlight" | "underline" | "ink" | null; ink?: InkStroke[]; color: string | null; page: number | null;
  rects: number[][]; segment_id: string | null; start_offset: number | null; end_offset: number | null;
  quote: string; body: string; category: string | null; status: string; tags: string[]; author: string;
  ai_answer: number; location: string | null; created: number;
};
export type Citation = { n: number; location: string; page: number; segment_id: string };
export type Answer = {
  id?: string; found: boolean; from_document: string; general_knowledge: string; citations: Citation[];
  mode: string; categories: string[]; searched_pages: number;
};
export type ChatRow = { id: string; role: "user" | "assistant"; content: string; meta: Answer | Record<string, never> };
export type Finding = {
  id: string; severity: string; category: string; title: string; location: string; quote: string;
  recommendation: string; page: number; segment_id: string | null;
};
export type Review = {
  id: string; status: string; position: number; total: number; message: string; findings: Finding[];
  config: { categories: string[]; mode: string };
};
export type Profile = { id: string; name: string; name_ar: string; persona: string; focus: string[]; builtin: boolean };

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(url: string) => call<T>("GET", url),
  post: <T>(url: string, body?: unknown) => call<T>("POST", url, body ?? {}),
  patch: <T>(url: string, body: unknown) => call<T>("PATCH", url, body),
  del: <T>(url: string) => call<T>("DELETE", url),
  upload: <T>(url: string, file: File, extra: Record<string, string> = {}) => {
    const form = new FormData();
    form.append("file", file);
    Object.entries(extra).forEach(([k, v]) => form.append(k, v));
    return call<T>("POST", url, form);
  },
};

export function download(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

const LETTERS: Record<string, string> = { "أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ـ": "" };
export function normalize(text: string) {
  return text.replace(/[أإآىةـ]/g, (c) => LETTERS[c] ?? c).replace(/[ً-ْ]/g, "")
    .replace(/[“”]/g, '"').replace(/\s+/g, " ").trim().toLowerCase();
}
