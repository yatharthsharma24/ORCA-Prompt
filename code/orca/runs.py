"""Persist finished runs to SQLite so results can be fetched and shared later."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    elapsed_s REAL,
    passed INTEGER,
    score REAL,
    error TEXT,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_created ON runs(created_at DESC);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RunStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def start(self, run_id: str, question: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO runs (id, question, status, created_at) VALUES (?,?,?,?)",
                      (run_id, question, "running", _now()))

    def finish(self, run_id: str, result: dict[str, Any]) -> None:
        v = result.get("verification", {})
        with self._lock, self._conn() as c:
            c.execute(
                "UPDATE runs SET status='done', finished_at=?, elapsed_s=?, passed=?, score=?, result_json=? WHERE id=?",
                (_now(), result.get("elapsed_s"), int(bool(v.get("passed"))), v.get("score"),
                 json.dumps(result, default=str), run_id),
            )

    def fail(self, run_id: str, error: str, status: str = "error") -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE runs SET status=?, finished_at=?, error=? WHERE id=?", (status, _now(), error[:2000], run_id))

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if r is None:
            return None
        out = {k: r[k] for k in r.keys() if k != "result_json"}
        out["result"] = json.loads(r["result_json"]) if r["result_json"] else None
        return out

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, question, status, created_at, elapsed_s, passed, score FROM runs "
                "ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
