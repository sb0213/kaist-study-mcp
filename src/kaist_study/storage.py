from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Iterator

from .models import now_utc


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        (root / "files").mkdir(exist_ok=True)
        self.db = sqlite3.connect(root / "kaist.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS records (
          type TEXT NOT NULL, source_id TEXT NOT NULL, course_id TEXT,
          payload TEXT NOT NULL, fetched_at TEXT NOT NULL, deleted INTEGER DEFAULT 0,
          PRIMARY KEY(type, source_id)
        );
        CREATE TABLE IF NOT EXISTS files (
          source_id TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL,
          path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
          fetched_at TEXT NOT NULL, source_url TEXT NOT NULL, status TEXT NOT NULL,
          PRIMARY KEY(source_id, version)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS text_index USING fts5(
          file_key UNINDEXED, source_id UNINDEXED, course_id UNINDEXED,
          location UNINDEXED, content, source_url UNINDEXED
        );
        CREATE TABLE IF NOT EXISTS jobs (
          job_id TEXT PRIMARY KEY, status TEXT NOT NULL, kind TEXT NOT NULL,
          started_at TEXT NOT NULL, finished_at TEXT, message TEXT
        );
        """)
        self.db.commit()

    def upsert(self, typ: str, source_id: str, payload: dict[str, Any], course_id: str | None = None) -> None:
        self.db.execute("INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?, ?, 0)",
                        (typ, source_id, course_id, json.dumps(payload, ensure_ascii=False), now_utc()))
        self.db.commit()

    def list_records(self, typ: str, course_id: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT payload FROM records WHERE type=? AND deleted=0"
        args: list[Any] = [typ]
        if course_id:
            q += " AND course_id=?"; args.append(course_id)
        return [json.loads(r[0]) for r in self.db.execute(q, args)]

    def get_record(self, typ: str, source_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT payload FROM records WHERE type=? AND source_id=?", (typ, source_id)).fetchone()
        return json.loads(row[0]) if row else None

    def save_bytes(self, source_id: str, name: str, data: bytes, source_url: str) -> dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        previous = self.db.execute("SELECT * FROM files WHERE source_id=? ORDER BY version DESC LIMIT 1", (source_id,)).fetchone()
        if previous and previous["sha256"] == digest and previous["status"] == "complete":
            return dict(previous) | {"deduplicated": True}
        version = (previous["version"] + 1) if previous else 1
        safe = re.sub(r"[^\w.() -]+", "_", Path(name).name).strip() or "download.bin"
        safe_source = re.sub(r"[^\w.-]+", "_", source_id).strip("._") or "source"
        target = self.root / "files" / f"{safe_source}.v{version}.{safe}"
        fd, temp_name = tempfile.mkstemp(prefix=".download-", dir=self.root / "files")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data); f.flush(); os.fsync(f.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name): os.unlink(temp_name)
        self.db.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (source_id, version, safe, str(target), digest, len(data), now_utc(), source_url, "complete"))
        self.db.commit()
        return {"source_id": source_id, "version": version, "path": str(target), "sha256": digest,
                "size": len(data), "source_url": source_url, "deduplicated": False}

    def files(self, source_id: str | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM files", []
        if source_id: q += " WHERE source_id=?"; args.append(source_id)
        return [dict(r) for r in self.db.execute(q, args)]

    def index(self, file_key: str, source_id: str, course_id: str, location: str, content: str, source_url: str) -> None:
        self.db.execute("DELETE FROM text_index WHERE file_key=?", (file_key,))
        self.db.execute("INSERT INTO text_index VALUES (?, ?, ?, ?, ?, ?)",
                        (file_key, source_id, course_id, location, content, source_url)); self.db.commit()

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT file_key, source_id, course_id, location, snippet(text_index, 4, '[', ']', '…', 20) AS snippet, source_url FROM text_index WHERE text_index MATCH ? LIMIT ?",
            (query, limit))]

    def job(self, job_id: str, kind: str, status: str, message: str | None = None) -> None:
        self.db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, COALESCE((SELECT started_at FROM jobs WHERE job_id=?), ?), ?, ?)",
                        (job_id, status, kind, job_id, now_utc(), now_utc() if status in ("done", "failed") else None, message)); self.db.commit()

    def job_status(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

