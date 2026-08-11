"""Small persistent response-state store for Responses tool continuations."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


JSON = dict[str, Any]


class SQLiteStateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        os.chmod(self.path, 0o600)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS responses (
                response_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self._db.commit()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def put(self, response_id: str, state: JSON) -> None:
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO responses(response_id, state_json, created_at) VALUES(?, ?, ?)",
                (response_id, payload, int(time.time())),
            )
            self._db.commit()

    def get(self, response_id: str) -> JSON | None:
        with self._lock:
            row = self._db.execute(
                "SELECT state_json FROM responses WHERE response_id = ?", (response_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def find_by_call_ids(self, call_ids: list[str]) -> JSON | None:
        expected = set(call_ids)
        if not expected:
            return None
        with self._lock:
            rows = self._db.execute(
                "SELECT state_json FROM responses ORDER BY created_at DESC LIMIT 256"
            ).fetchall()
        for row in rows:
            state = json.loads(row[0])
            if expected.issubset(set(state.get("pending_call_ids") or [])):
                return state
        return None
