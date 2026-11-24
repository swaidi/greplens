"""SQLite storage. One connection per call keeps it safe across worker threads."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .config import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, categories TEXT NOT NULL DEFAULT '[]',
  created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, filename TEXT NOT NULL, kind TEXT NOT NULL,
  sha256 TEXT, pages INTEGER DEFAULT 0, ocr_pages INTEGER DEFAULT 0, status TEXT DEFAULT 'queued',
  progress REAL DEFAULT 0, message TEXT DEFAULT '', revision_of TEXT, created REAL);
CREATE TABLE IF NOT EXISTS segments (
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL, seq INTEGER, page INTEGER, approx_page INTEGER DEFAULT 0,
  article TEXT, clause TEXT, paragraph INTEGER, location TEXT, text TEXT, bbox TEXT);
CREATE INDEX IF NOT EXISTS seg_doc ON segments(document_id, seq);
CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY, workspace_id TEXT, document_id TEXT, mark TEXT, color TEXT, page INTEGER,
  rects TEXT, segment_id TEXT, start_offset INTEGER, end_offset INTEGER, quote TEXT, body TEXT,
  category TEXT, status TEXT DEFAULT 'open', tags TEXT DEFAULT '[]', author TEXT, ai_answer INTEGER DEFAULT 0,
  location TEXT, ink TEXT DEFAULT '[]', handwriting TEXT DEFAULT '[]', created REAL, updated REAL);
CREATE INDEX IF NOT EXISTS note_doc ON notes(document_id);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY, document_id TEXT, config TEXT, status TEXT, position INTEGER DEFAULT 0,
  total INTEGER DEFAULT 0, message TEXT DEFAULT '', created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS findings (
  id TEXT PRIMARY KEY, review_id TEXT, document_id TEXT, severity TEXT, category TEXT, title TEXT,
  location TEXT, quote TEXT, recommendation TEXT, page INTEGER, segment_id TEXT, created REAL);
CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY, document_id TEXT, role TEXT, content TEXT, meta TEXT, created REAL);
CREATE TABLE IF NOT EXISTS glossary (
  id TEXT PRIMARY KEY, en TEXT NOT NULL, ar TEXT NOT NULL);
"""

JSON_FIELDS = {"categories", "rects", "tags", "config", "meta", "bbox", "ink", "handwriting"}


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


@contextmanager
def connect():
    conn = sqlite3.connect(data_dir() / "greplens.db", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


MIGRATIONS = [("notes", "ink", "TEXT DEFAULT '[]'"), ("notes", "handwriting", "TEXT DEFAULT '[]'")]


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, column, kind in MIGRATIONS:  # columns added after 0.1.0
            if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def _decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    for key in JSON_FIELDS & item.keys():
        if isinstance(item[key], str):
            try:
                item[key] = json.loads(item[key])
            except ValueError:
                pass
    return item


def _encode(values: dict) -> dict:
    return {k: (json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS and not isinstance(v, str) else v)
            for k, v in values.items()}


def insert(table: str, values: dict) -> dict:
    values = dict(values)
    values.setdefault("id", new_id())
    encoded = _encode(values)
    cols = ", ".join(encoded)
    marks = ", ".join("?" for _ in encoded)
    with connect() as conn:
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(encoded.values()))
    return values


def insert_many(table: str, rows: list[dict]) -> None:
    if not rows:
        return
    encoded = [_encode(r) for r in rows]
    cols = list(encoded[0])
    with connect() as conn:
        conn.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                         [[r.get(c) for c in cols] for r in encoded])


def update(table: str, row_id: str, values: dict) -> None:
    if not values:
        return
    encoded = _encode(values)
    sets = ", ".join(f"{k} = ?" for k in encoded)
    with connect() as conn:
        conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*encoded.values(), row_id])


def get(table: str, row_id: str) -> dict | None:
    with connect() as conn:
        return _decode(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone())


def query(sql: str, params: tuple = ()) -> list[dict]:
    with connect() as conn:
        return [_decode(r) for r in conn.execute(sql, params).fetchall()]


def execute(sql: str, params: tuple = ()) -> None:
    with connect() as conn:
        conn.execute(sql, params)
