"""Read-only access to the marine database and its dataset catalog."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class QueryError(ValueError):
    pass


@dataclass(frozen=True)
class Dataset:
    dataset_id: str
    table_name: str
    title: str
    description: str
    columns: dict[str, str]
    publisher: str
    citation: str
    license: str | None
    url: str | None
    synthetic: bool

    def summary(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "table": self.table_name,
            "title": self.title,
            "synthetic": self.synthetic,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.summary(), "description": self.description, "columns": self.columns,
                "publisher": self.publisher, "citation": self.citation,
                "license": self.license, "url": self.url}


class MarineStore:
    """Opens the SQLite file read-only and rejects anything but SELECT."""

    def __init__(self, path: Path, *, row_limit: int = 200, timeout_s: float = 5.0) -> None:
        if not Path(path).exists():
            raise FileNotFoundError(
                f"Marine database not found at {path}. Build it with: python -m orca.data.generate"
            )
        self.path = Path(path)
        self.row_limit = row_limit
        self.timeout_s = timeout_s
        self._datasets = self._load_catalog()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_catalog(self) -> dict[str, Dataset]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM _catalog ORDER BY dataset_id").fetchall()
        out = {}
        for r in rows:
            out[r["dataset_id"]] = Dataset(
                dataset_id=r["dataset_id"], table_name=r["table_name"], title=r["title"],
                description=r["description"] or "", columns=json.loads(r["columns_json"]),
                publisher=r["publisher"] or "", citation=r["citation"], license=r["license"],
                url=r["url"], synthetic=bool(r["synthetic"]),
            )
        return out

    # --- catalog -------------------------------------------------------
    @property
    def datasets(self) -> list[Dataset]:
        return list(self._datasets.values())

    def dataset(self, key: str) -> Dataset:
        key = key.strip()
        if key in self._datasets:
            return self._datasets[key]
        for d in self._datasets.values():
            if d.table_name == key or d.dataset_id.lower() == key.lower():
                return d
        raise QueryError(
            f"Unknown dataset '{key}'. Known: "
            + ", ".join(f"{d.dataset_id} ({d.table_name})" for d in self._datasets.values())
        )

    def datasets_for_sql(self, sql: str) -> list[Dataset]:
        low = sql.lower()
        return [d for d in self._datasets.values() if re.search(rf"\b{d.table_name.lower()}\b", low)]

    def distinct_values(self, table: str, column: str, limit: int = 40) -> list[Any]:
        ds = self.dataset(table)
        if column not in ds.columns:
            raise QueryError(f"Column '{column}' not in {ds.table_name}. Columns: {', '.join(ds.columns)}")
        rows = self.query(
            f'SELECT DISTINCT "{column}" AS v FROM "{ds.table_name}" ORDER BY 1 LIMIT {int(limit)}'
        )
        return [r["v"] for r in rows]

    # --- querying ------------------------------------------------------
    def query(self, sql: str, params: tuple | list = (), max_rows: int | None = None) -> list[dict[str, Any]]:
        """Run a read-only SELECT.

        Returns at most ``max_rows + 1`` rows (default ``row_limit + 1``) so callers
        can detect truncation. Analysis tools pass a large ``max_rows`` because they
        aggregate whole series; only agent-facing listings use the small default.
        """
        limit = self.row_limit if max_rows is None else max_rows
        sql = sql.strip().rstrip(";").strip()
        if not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
            raise QueryError("Only SELECT (or WITH ... SELECT) queries are allowed.")
        if ";" in sql:
            raise QueryError("Only one statement per query.")
        conn = self._connect()
        deadline = time.monotonic() + self.timeout_s

        def authorizer(action, arg1, arg2, dbname, source):  # noqa: ARG001
            if action in _ALLOWED_ACTIONS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        conn.set_authorizer(authorizer)
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        try:
            cur = conn.execute(sql, params)
            rows = cur.fetchmany(limit + 1)
        except sqlite3.Error as exc:
            raise QueryError(f"SQL error: {exc}") from exc
        finally:
            conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def check_ident(name: str) -> str:
        if not _IDENT.match(name):
            raise QueryError(f"Invalid identifier: {name!r}")
        return name
