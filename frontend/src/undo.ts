import { api, Note } from "./api";

/** One step the user can undo and redo: adding, changing or deleting a note, mark or ink. */
type Step = { undo: () => Promise<unknown>; redo: () => Promise<unknown> };

const done: Step[] = [];
const undone: Step[] = [];
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((fn) => fn());
let busy = false;

export const history = {
  push(step: Step) {
    done.push(step);
    if (done.length > 200) done.shift();
    undone.length = 0;
    emit();
  },
  canUndo: () => done.length > 0,
  canRedo: () => undone.length > 0,
  async undo() {
    const step = done.pop();
    if (!step || busy) { if (step) done.push(step); return false; }
    busy = true;
    try { await step.undo(); undone.push(step); } finally { busy = false; emit(); }
    return true;
  },
  async redo() {
    const step = undone.pop();
    if (!step || busy) { if (step) undone.push(step); return false; }
    busy = true;
    try { await step.redo(); done.push(step); } finally { busy = false; emit(); }
    return true;
  },
  clear() { done.length = 0; undone.length = 0; emit(); },
  subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; },
};

const pick = (n: Note, keys: string[]) => Object.fromEntries(keys.map((k) => [k, (n as Record<string, unknown>)[k] ?? null]));

/** Note changes that are recorded so Ctrl+Z, the Undo button and gestures can take them back. */
export const notesApi = {
  async create(body: Partial<Note>): Promise<Note> {
    const note = await api.post<Note>("/api/notes", body);
    history.push({ undo: () => api.del(`/api/notes/${note.id}`), redo: () => api.post("/api/notes/restore", note) });
    return note;
  },
  async patch(note: Note, changes: Partial<Note>): Promise<Note> {
    const keys = Object.keys(changes);
    if (keys.includes("handwriting")) keys.push("handwriting_text", "handwriting_lang"); // undo restores the text read, too
    const before = pick(note, keys);
    const after = await api.patch<Note>(`/api/notes/${note.id}`, changes);
    const redo = pick(after, keys);
    history.push({ undo: () => api.patch(`/api/notes/${note.id}`, before), redo: () => api.patch(`/api/notes/${note.id}`, redo) });
    return after;
  },
  async remove(note: Note): Promise<void> {
    await api.del(`/api/notes/${note.id}`);
    history.push({ undo: () => api.post("/api/notes/restore", note), redo: () => api.del(`/api/notes/${note.id}`) });
  },
};
