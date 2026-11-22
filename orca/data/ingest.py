"""Load a real CSV dataset into the ORCA database and register it in the catalog.

Example (CMFRI landings exported to CSV):

    python -m orca.data.ingest landings.csv --table fish_landings --dataset-id D-FISH \
        --title "Marine fish landings by state, CMFRI" \
        --citation "ICAR-CMFRI (2024). Marine Fish Landings in India 2023." \
        --url https://www.cmfri.org.in --license "see source" \
        --describe landings_tonnes="landings in tonnes" --replace

Using an existing table name with --replace swaps the synthetic table for real data;
the agents pick up the new catalog entry on the next server start.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from pathlib import Path

from orca.config import get_settings

_IDENT = re.compile(r"[^A-Za-z0-9_]+")


def _clean(name: str) -> str:
    name = _IDENT.sub("_", name.strip()).strip("_").lower()
    return name if name and not name[0].isdigit() else f"c_{name}"


def _convert(value: str):
    v = value.strip()
    if v == "" or v.upper() in ("NA", "NAN", "NULL"):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def _sql_type(values: list) -> str:
    kinds = {type(v) for v in values if v is not None}
    if kinds <= {int}:
        return "INTEGER"
    if kinds <= {int, float}:
        return "REAL"
    return "TEXT"


def ingest(
    csv_path: Path, db: Path, table: str, dataset_id: str, title: str, citation: str,
    *, url: str | None = None, license_: str | None = None, publisher: str | None = None,
    description: str = "", describe: dict[str, str] | None = None, replace: bool = False,
) -> int:
    table = _clean(table)
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = [_clean(h) for h in next(reader)]
        rows = [[_convert(c) for c in row] for row in reader if any(c.strip() for c in row)]
    if not rows:
        raise SystemExit("CSV has no data rows.")
    ragged = [i + 2 for i, r in enumerate(rows) if len(r) != len(header)]
    if ragged:
        raise SystemExit(f"Rows {ragged[:10]} do not have {len(header)} columns.")
    cols = list(zip(*rows, strict=True))
    types = [_sql_type(list(c)) for c in cols]
    conn = sqlite3.connect(db)
    try:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if exists and not replace:
            raise SystemExit(f"Table {table} exists; pass --replace to overwrite it.")
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        conn.execute(f'CREATE TABLE "{table}" ({", ".join(f"{chr(34)}{h}{chr(34)} {t}" for h, t in zip(header, types, strict=True))})')
        conn.executemany(f'INSERT INTO "{table}" VALUES ({",".join("?" * len(header))})', rows)
        col_docs = {h: (describe or {}).get(h, h.replace("_", " ")) for h in header}
        conn.execute("DELETE FROM _catalog WHERE dataset_id=? OR table_name=?", (dataset_id, table))
        conn.execute(
            "INSERT INTO _catalog VALUES (?,?,?,?,?,?,?,?,?,0)",
            (dataset_id, table, title, description, json.dumps(col_docs), publisher or "", citation, license_, url),
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Load a CSV into the ORCA marine database.")
    p.add_argument("csv", type=Path)
    p.add_argument("--db", type=Path, default=get_settings().data_db)
    p.add_argument("--table", required=True)
    p.add_argument("--dataset-id", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--citation", required=True, help="full citation shown in answers")
    p.add_argument("--url")
    p.add_argument("--license")
    p.add_argument("--publisher")
    p.add_argument("--description", default="")
    p.add_argument("--describe", action="append", default=[], metavar="COL=TEXT", help="column description (repeatable)")
    p.add_argument("--replace", action="store_true")
    a = p.parse_args()
    describe = dict(d.split("=", 1) for d in a.describe if "=" in d)
    n = ingest(a.csv, a.db, a.table, a.dataset_id, a.title, a.citation, url=a.url, license_=a.license,
               publisher=a.publisher, description=a.description, describe=describe, replace=a.replace)
    print(f"loaded {n} rows into {a.table} ({a.dataset_id})")


if __name__ == "__main__":
    main()
