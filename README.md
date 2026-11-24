# GrepLens

Offline document review, Q&A and annotation with local AI models. Arabic and English.

GrepLens reads PDF, Word, Excel, PowerPoint and text files; answers questions with exact citations;
runs category-based reviews (Legal, Engineering, Students, Technology, General, or your own);
and lets you highlight, underline and add margin notes. Everything runs on your own computer:
no document leaves it.

## Features

- **Import:** PDF, DOCX, XLSX, PPTX, TXT/MD/CSV, up to about 1,000 pages. Scanned PDF pages are read by the
  built-in GrepAll OCR (English and Arabic) only when a page has no text layer.
- **Exact locations:** volume, article, clause, paragraph and page for every citation and finding
  (Word files show an approximate page, because Word has no fixed pages).
- **Ask:** Category mode (document facts plus separately labelled general knowledge) or Doc Only mode
  (strictly from the document, or "Not found in the document"). Follow-up questions keep context.
- **Review (LangGraph):** open-ended by category (two can be combined), a fixed checklist, or a specific
  instruction. Each finding is verified against the real text before it is shown; rule checks flag
  placeholders left blank, cross-references to missing clauses, and amounts stated more than once.
  Reviews run in the background and can be paused and resumed.
- **Mark up:** highlight or underline in five colors, margin notes with categories (Risk, Query,
  Clarification, Action, Info), Open/Resolved status and tags. Notes carry over to a revised version.
- **Translate:** select a word or phrase, then right-click or use the toolbar. Arabic ↔ English with the
  surrounding clause as context, plus a fixed-term glossary.
- **Export and share:** Word (.docx) with comments; Workspace packages (.greplens) that colleagues import
  and merge, with each note keeping its author.
- **Setup wizard:** device summary (processor, RAM, GPU, drives), model recommendations, reuse of models
  already installed by Ollama or LM Studio, choice of drive, resumable verified downloads.
- **Interface:** English by default, Arabic with full right-to-left layout. Daily update check.

## Architecture

| Part | Technology |
|---|---|
| Desktop shell | Tauri 2 (`src-tauri`), starts the back end as a sidecar |
| Interface | React + TypeScript + Vite, pdf.js viewer (`frontend`) |
| Back end | Python FastAPI on 127.0.0.1:8710 (`backend/greplens`) |
| Retrieval | LlamaIndex nodes with location metadata, stored in Chroma |
| Review workflow | LangGraph (`backend/greplens/review`) |
| AI engine | llama.cpp `llama-server` (bundled), or an existing Ollama installation |
| Models | Qwen3 8B (Q&A, translation), Qwen3 14B (review), BGE-M3 (search); list in `models.json` |
| OCR | GrepAll engine (PyMuPDF + Tesseract language data) |
| Storage | SQLite and Chroma in the user data folder; models in a folder the user chooses |

Data folder: `%LOCALAPPDATA%\GrepLens` on Windows, `~/.local/share/greplens` on Linux.

## Build the Windows installer

The simplest route is GitHub Actions: push a tag such as `v0.1.0`, or run the
**Build Windows installer** workflow by hand. It tests the back end, bundles llama.cpp and the OCR
language data, and produces `GrepLens_<version>_x64-setup.exe` (a draft release for tags).

## Run in development

Requirements: Python 3.12, Node 22, and for the desktop shell Rust (stable) with the Tauri prerequisites.

```bash
# 1. Interface
cd frontend
npm ci
npm run build            # outputs to backend/greplens/static

# 2. Back end (opens the interface in your browser)
cd ../backend
python -m venv .venv
.venv\Scripts\activate   # Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m greplens --browser
```

Without models, set `GREPLENS_FAKE_AI=1` to use a deterministic stand-in AI for interface work.
For real answers, put `llama-server` on the PATH (or in `src-tauri/resources/engine`) and run the setup wizard.

Tests: `cd backend && python -m pytest -q tests`

Desktop shell: `cd backend && pip install pyinstaller && python build_backend.py`, then
`cd ../src-tauri && npx @tauri-apps/cli@2 build`.

## License

AGPL-3.0. OCR code ported from GrepAll by the same author. Third-party components keep their own
licenses: llama.cpp (MIT), Qwen3 models (Apache-2.0), BGE-M3 (MIT), Tesseract data (Apache-2.0),
pdf.js (Apache-2.0), PyMuPDF (AGPL-3.0), LlamaIndex (MIT), LangGraph (MIT), Chroma (Apache-2.0).
