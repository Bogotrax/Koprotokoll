"""SQLite-Speicher mit FTS5-Volltextsuche über alle Transkripte."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    source_name TEXT,
    audio_path  TEXT,
    duration    REAL,
    status      TEXT NOT NULL DEFAULT 'queued',
    progress    REAL NOT NULL DEFAULT 0,
    error       TEXT,
    num_speakers INTEGER,
    speakers    TEXT NOT NULL DEFAULT '{}',
    summary     TEXT,
    meta        TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS segments (
    id         INTEGER PRIMARY KEY,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    start      REAL NOT NULL,
    end        REAL NOT NULL,
    speaker    TEXT NOT NULL,
    text       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS segments_meeting ON segments(meeting_id, idx);
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(
    text, meeting_id UNINDEXED, segment_id UNINDEXED, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS bookmarks (
    id         INTEGER PRIMARY KEY,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    start      REAL NOT NULL,
    end        REAL NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'soundbite',
    note       TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    prefix     TEXT NOT NULL,
    key_hash   TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_used  REAL
);
CREATE TABLE IF NOT EXISTS webhooks (
    id         INTEGER PRIMARY KEY,
    url        TEXT NOT NULL,
    secret     TEXT NOT NULL,
    events     TEXT NOT NULL DEFAULT '["*"]',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bots (
    id          INTEGER PRIMARY KEY,
    meeting_url TEXT NOT NULL,
    platform    TEXT NOT NULL,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'scheduled',
    error       TEXT,
    meeting_id  INTEGER REFERENCES meetings(id) ON DELETE SET NULL,
    calendar_event_id TEXT UNIQUE,
    join_at     REAL NOT NULL,
    created_at  REAL NOT NULL,
    ended_at    REAL,
    meta        TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(meetings)")}
            if "meta" not in cols:  # Migration von Version 1
                self._conn.execute("ALTER TABLE meetings ADD COLUMN meta TEXT NOT NULL DEFAULT '{}'")

    # -- Meetings ---------------------------------------------------------
    def create_meeting(self, title: str, source_name: str | None, num_speakers: int | None = None,
                       meta: dict | None = None) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO meetings (title, created_at, source_name, num_speakers, meta) VALUES (?, ?, ?, ?, ?)",
                (title, time.time(), source_name, num_speakers, json.dumps(meta or {}, ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    def update_meeting(self, meeting_id: int, **fields: Any) -> None:
        if not fields:
            return
        for key in ("speakers", "summary", "meta"):
            if key in fields and not isinstance(fields[key], str) and fields[key] is not None:
                fields[key] = json.dumps(fields[key], ensure_ascii=False)
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE meetings SET {cols} WHERE id = ?", (*fields.values(), meeting_id))

    def get_meeting(self, meeting_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        return _meeting(row) if row else None

    def list_meetings(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM meetings ORDER BY created_at DESC").fetchall()
        return [_meeting(r) for r in rows]

    def delete_meeting(self, meeting_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM segments_fts WHERE meeting_id = ?", (meeting_id,))
            self._conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))

    # -- Transkript -------------------------------------------------------
    def replace_segments(self, meeting_id: int, segments: list[dict]) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM segments_fts WHERE meeting_id = ?", (meeting_id,))
            self._conn.execute("DELETE FROM segments WHERE meeting_id = ?", (meeting_id,))
            for i, s in enumerate(segments):
                cur = self._conn.execute(
                    "INSERT INTO segments (meeting_id, idx, start, end, speaker, text) VALUES (?, ?, ?, ?, ?, ?)",
                    (meeting_id, i, s["start"], s["end"], s["speaker"], s["text"]),
                )
                self._conn.execute(
                    "INSERT INTO segments_fts (text, meeting_id, segment_id) VALUES (?, ?, ?)",
                    (s["text"], meeting_id, cur.lastrowid),
                )

    def get_segments(self, meeting_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, idx, start, end, speaker, text FROM segments WHERE meeting_id = ? ORDER BY idx",
                (meeting_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str, meeting_id: int | None = None, limit: int = 50,
               any_word: bool = False) -> list[dict]:
        fts_query = _fts_query(query, any_word)
        if not fts_query:
            return []
        sql = (
            "SELECT s.meeting_id, s.id AS segment_id, s.start, s.end, s.speaker, m.title, m.speakers,"
            " snippet(segments_fts, 0, '[[', ']]', ' … ', 24) AS snippet"
            " FROM segments_fts JOIN segments s ON s.id = segments_fts.segment_id"
            " JOIN meetings m ON m.id = s.meeting_id"
            " WHERE segments_fts MATCH ?"
        )
        params: list[Any] = [fts_query]
        if meeting_id is not None:
            sql += " AND s.meeting_id = ?"
            params.append(meeting_id)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            names = json.loads(d.pop("speakers") or "{}")
            d["speaker_name"] = names.get(d["speaker"], d["speaker"])
            out.append(d)
        return out

    # -- Markierungen (Soundbites, Action Items, Kommentare) ------------------
    def add_bookmark(self, meeting_id: int, start: float, end: float, kind: str, note: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO bookmarks (meeting_id, start, end, kind, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (meeting_id, start, end, kind, note, time.time()),
            )
            return int(cur.lastrowid)

    def list_bookmarks(self, meeting_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM bookmarks WHERE meeting_id = ? ORDER BY start", (meeting_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_bookmark(self, meeting_id: int, bookmark_id: int) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM bookmarks WHERE id = ? AND meeting_id = ?", (bookmark_id, meeting_id)
            )
            return cur.rowcount > 0

    # -- API-Schlüssel ----------------------------------------------------------
    def add_api_key(self, name: str, prefix: str, key_hash: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO api_keys (name, prefix, key_hash, created_at) VALUES (?, ?, ?, ?)",
                (name, prefix, key_hash, time.time()),
            )
            return int(cur.lastrowid)

    def find_api_key(self, key_hash: str) -> dict | None:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)).fetchone()
            if row:
                self._conn.execute("UPDATE api_keys SET last_used = ? WHERE id = ?", (time.time(), row["id"]))
        return dict(row) if row else None

    def list_api_keys(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, prefix, created_at, last_used FROM api_keys ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def delete_api_key(self, key_id: int) -> bool:
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,)).rowcount > 0

    # -- Webhooks -------------------------------------------------------------------
    def add_webhook(self, url: str, secret: str, events: list[str]) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO webhooks (url, secret, events, created_at) VALUES (?, ?, ?, ?)",
                (url, secret, json.dumps(events), time.time()),
            )
            return int(cur.lastrowid)

    def list_webhooks(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM webhooks ORDER BY id").fetchall()
        return [{**dict(r), "events": json.loads(r["events"])} for r in rows]

    def delete_webhook(self, webhook_id: int) -> bool:
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,)).rowcount > 0

    # -- Bots ---------------------------------------------------------------------------
    def create_bot(self, meeting_url: str, platform: str, title: str, join_at: float,
                   calendar_event_id: str | None = None, meta: dict | None = None) -> int | None:
        """Legt einen Bot an; None, wenn für das Kalenderereignis schon einer existiert."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO bots (meeting_url, platform, title, join_at, calendar_event_id, created_at, meta)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (meeting_url, platform, title, join_at, calendar_event_id, time.time(),
                 json.dumps(meta or {}, ensure_ascii=False)),
            )
            return int(cur.lastrowid) if cur.rowcount else None

    def update_bot(self, bot_id: int, **fields: Any) -> None:
        if "meta" in fields and not isinstance(fields["meta"], str):
            fields["meta"] = json.dumps(fields["meta"], ensure_ascii=False)
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._conn:
            self._conn.execute(f"UPDATE bots SET {cols} WHERE id = ?", (*fields.values(), bot_id))

    def get_bot(self, bot_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
        return _bot(row) if row else None

    def list_bots(self, statuses: tuple[str, ...] | None = None, limit: int = 100) -> list[dict]:
        sql, params = "SELECT * FROM bots", []
        if statuses:
            sql += f" WHERE status IN ({','.join('?' * len(statuses))})"
            params += list(statuses)
        sql += " ORDER BY join_at DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (*params, limit)).fetchall()
        return [_bot(r) for r in rows]

    # -- Einstellungen / Integrationen ------------------------------------------------------
    def get_kv(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_kv(self, key: str, value: Any) -> None:
        with self._lock, self._conn:
            if value is None:
                self._conn.execute("DELETE FROM kv WHERE key = ?", (key,))
            else:
                self._conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
                                   (key, json.dumps(value, ensure_ascii=False)))


def _bot(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["meta"] = json.loads(d["meta"] or "{}")
    return d


def _meeting(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["speakers"] = json.loads(d["speakers"] or "{}")
    d["summary"] = json.loads(d["summary"]) if d["summary"] else None
    d["meta"] = json.loads(d.get("meta") or "{}")
    return d


def _fts_query(query: str, any_word: bool = False) -> str:
    """Nutzereingabe in eine sichere FTS5-Abfrage übersetzen (Präfixsuche je Wort)."""
    words = [w for w in (re.sub(r"[^\w-]", "", w) for w in query.split()) if w]
    return (" OR " if any_word else " ").join(f'"{w}"*' for w in words)
