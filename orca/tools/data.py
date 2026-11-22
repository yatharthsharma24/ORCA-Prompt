"""Catalog, structured query, raw SQL and species lookup tools."""

from __future__ import annotations

import re
from typing import Any

from orca.data.store import Dataset, MarineStore, QueryError
from orca.evidence import Source, tidy
from orca.tools.base import Tool, ToolContext, ToolError, ToolResult

AGGS = {"avg", "sum", "min", "max", "count"}
_EXPR = re.compile(r"^\s*(?:(avg|sum|min|max|count)\s*\(\s*(\*|[A-Za-z_][A-Za-z0-9_]*)\s*\)|([A-Za-z_][A-Za-z0-9_]*))\s*$", re.I)
_OPS = {">": ">", ">=": ">=", "<": "<", "<=": "<=", "=": "=", "==": "=", "!=": "!=", "like": "LIKE"}


def dataset_source(ds: Dataset) -> Source:
    return Source("dataset", ds.dataset_id, ds.title, ds.citation, ds.url, ds.synthetic)


def resolve_dataset(store: MarineStore, args: dict[str, Any]) -> Dataset:
    key = args.get("dataset") or args.get("table")
    if not key:
        raise ToolError("Missing 'dataset' (e.g. \"D-OCEAN\" or \"ocean_monthly\").")
    try:
        return store.dataset(str(key))
    except QueryError as exc:
        raise ToolError(str(exc)) from exc


def check_column(ds: Dataset, col: str) -> str:
    if col not in ds.columns:
        raise ToolError(f"Column '{col}' is not in {ds.table_name}. Columns: {', '.join(ds.columns)}")
    return col


def build_where(ds: Dataset, where: dict[str, Any] | None) -> tuple[str, list[Any]]:
    if not where:
        return "", []
    if not isinstance(where, dict):
        raise ToolError("'where' must be an object like {\"region\": \"SE Arabian Sea\", \"year\": {\">=\": 2010}}")
    clauses, params = [], []
    for col, cond in where.items():
        check_column(ds, col)
        q = f'"{col}"'
        if isinstance(cond, list):
            if not cond:
                raise ToolError(f"Empty list for '{col}'.")
            clauses.append(f"{q} IN ({','.join('?' * len(cond))})")
            params.extend(cond)
        elif isinstance(cond, dict):
            for op, val in cond.items():
                op_l = str(op).lower().strip()
                if op_l == "between":
                    if not (isinstance(val, list) and len(val) == 2):
                        raise ToolError("'between' needs a two-item list, e.g. [2010, 2020].")
                    clauses.append(f"{q} BETWEEN ? AND ?")
                    params.extend(val)
                elif op_l == "in":
                    clauses.append(f"{q} IN ({','.join('?' * len(val))})")
                    params.extend(val)
                elif op_l in _OPS:
                    clauses.append(f"{q} {_OPS[op_l]} ?")
                    params.append(val)
                else:
                    raise ToolError(f"Unknown operator '{op}'. Use one of =, !=, >, >=, <, <=, like, in, between.")
        elif cond is None:
            clauses.append(f"{q} IS NULL")
        else:
            clauses.append(f"{q} = ?")
            params.append(cond)
    return " WHERE " + " AND ".join(clauses), params


def parse_expr(ds: Dataset, expr: str) -> tuple[str, str]:
    """Return (sql_expression, alias) for 'col' or 'agg(col)'."""
    m = _EXPR.match(str(expr))
    if not m:
        raise ToolError(f"Bad column expression '{expr}'. Use a column name or avg/sum/min/max/count(column).")
    agg, inner, plain = m.groups()
    if plain:
        check_column(ds, plain)
        return f'"{plain}"', plain
    agg = agg.lower()
    if inner == "*":
        return "COUNT(*)", "n_rows"
    check_column(ds, inner)
    sql = f'ROUND({agg.upper()}("{inner}"), 4)' if agg in ("avg", "sum") else f'{agg.upper()}("{inner}")'
    return sql, f"{agg}_{inner}"


def format_rows(rows: list[dict[str, Any]], limit_note: str = "") -> str:
    if not rows:
        return "(no rows)"
    cols = list(rows[0].keys())
    lines = [" | ".join(cols)]
    for r in rows:
        lines.append(" | ".join("NULL" if r[c] is None else str(r[c]) for c in cols))
    return "\n".join(lines) + limit_note


def empty_hint(store: MarineStore, ds: Dataset, where: dict[str, Any] | None) -> str:
    hints = []
    for col, cond in (where or {}).items():
        if isinstance(cond, (str, list)) and col in ds.columns:
            try:
                vals = store.distinct_values(ds.table_name, col, limit=30)
            except QueryError:
                continue
            hints.append(f"Valid values for {col}: {vals}")
    return " No rows matched. " + " ".join(hints) if hints else " No rows matched the filters."


class ListDatasets(Tool):
    name = "list_datasets"
    description = "List the datasets in the marine database with their ids, tables and titles."
    example: dict[str, Any] = {}

    async def run(self, args, ctx: ToolContext) -> ToolResult:
        lines = [
            f"{d.dataset_id} | table {d.table_name} | {d.title}" + (" | SYNTHETIC demo data" if d.synthetic else "")
            for d in ctx.store.datasets
        ]
        return ToolResult("\n".join(lines), record=False)


class DescribeDataset(Tool):
    name = "describe_dataset"
    description = "Show a dataset's columns, units, year range and the valid values of its text columns."
    example = {"dataset": "D-OCEAN"}

    async def run(self, args, ctx: ToolContext) -> ToolResult:
        ds = resolve_dataset(ctx.store, args)
        lines = [f"{ds.dataset_id} ({ds.table_name}): {ds.title}", ds.description, "Columns:"]
        lines += [f"  {c}: {desc}" for c, desc in ds.columns.items()]
        if "year" in ds.columns:
            r = ctx.store.query(f'SELECT MIN(year) AS lo, MAX(year) AS hi FROM "{ds.table_name}"')[0]
            lines.append(f"Years covered: {r['lo']}-{r['hi']}")
        for col in ("region", "state", "scientific_name", "site", "method"):
            if col in ds.columns:
                lines.append(f"Values of {col}: {ctx.store.distinct_values(ds.table_name, col, 30)}")
        if ds.synthetic:
            lines.append("NOTE: synthetic demo data, not real observations.")
        return ToolResult("\n".join(lines), record=False)


class QueryData(Tool):
    name = "query_data"
    description = (
        "Filter and aggregate one dataset without writing SQL. 'columns' takes column names or "
        "avg/sum/min/max/count(column); 'where' takes exact values, lists (= any of these exact values), or "
        "operators (>=, <=, >, <, !=, like, between); for a year range use {\"year\": {\"between\": [2014, 2024]}}; "
        "'group_by' and 'order_by' take column names "
        "(prefix order_by with '-' for descending)."
    )
    example = {
        "dataset": "D-OCEAN",
        "columns": ["year", "avg(chlorophyll_mg_m3)"],
        "where": {"region": "SE Arabian Sea", "month": [6, 7, 8, 9]},
        "group_by": ["year"],
        "order_by": "year",
        "limit": 50,
    }

    async def run(self, args, ctx: ToolContext) -> ToolResult:
        ds = resolve_dataset(ctx.store, args)
        cols = args.get("columns") or ["*"]
        if isinstance(cols, str):
            cols = [c.strip() for c in cols.split(",")]
        group = args.get("group_by") or []
        if isinstance(group, str):
            group = [group]
        if cols == ["*"]:
            select = "*"
        else:
            # Always return grouping columns, otherwise grouped rows are unlabeled.
            cols = [g for g in group if g not in cols] + list(cols)
            if group:
                loose = [c for c in cols if _EXPR.match(str(c)) and _EXPR.match(str(c)).group(3) and c not in group]
                if loose:
                    raise ToolError(
                        f"With group_by {group}, column(s) {loose} must be aggregated, e.g. "
                        f"\"max({loose[0]})\" or \"avg({loose[0]})\", or added to group_by."
                    )
            parts = []
            for c in cols:
                sql, alias = parse_expr(ds, c)
                parts.append(f'{sql} AS "{alias}"')
            select = ", ".join(parts)
        where_sql, params = build_where(ds, args.get("where"))
        group_sql = (" GROUP BY " + ", ".join(f'"{check_column(ds, g)}"' for g in group)) if group else ""
        order_sql = ""
        if args.get("order_by"):
            ob = str(args["order_by"]).strip()
            desc = ob.startswith("-") or ob.lower().endswith(" desc")
            ob = ob.lstrip("-").replace(" desc", "").replace(" DESC", "").replace(" asc", "").strip()
            if ob in ds.columns:
                order_sql = f' ORDER BY "{ob}"' + (" DESC" if desc else "")
            else:
                order_sql = f" ORDER BY {parse_expr(ds, ob)[0]}" + (" DESC" if desc else "")
        limit = max(1, min(int(args.get("limit", 100)), ctx.settings.sql_row_limit))
        sql = f'SELECT {select} FROM "{ds.table_name}"{where_sql}{group_sql}{order_sql} LIMIT {limit}'
        try:
            rows = tidy(ctx.store.query(sql, params))
        except QueryError as exc:
            raise ToolError(str(exc)) from exc
        if not rows:
            return ToolResult(f"Query: {sql}\nParams: {params}\n" + empty_hint(ctx.store, ds, args.get("where")).strip(), record=False)
        note = f"\n({len(rows)} rows; limit {limit})"
        return ToolResult(
            f"{ds.dataset_id} rows:\n" + format_rows(rows, note),
            data={"sql": sql, "params": params, "rows": rows},
            sources=[dataset_source(ds)],
        )


class RunSQL(Tool):
    name = "run_sql"
    description = (
        "Run one read-only SQLite SELECT across any tables (joins, CTEs, CASE allowed). "
        "Use when query_data cannot express the question."
    )
    example = {
        "sql": "SELECT f.year, f.landings_tonnes, ROUND(AVG(o.chlorophyll_mg_m3),3) AS monsoon_chl "
        "FROM fish_landings f JOIN ocean_monthly o ON o.year = f.year - 1 "
        "WHERE f.state='Kerala' AND f.scientific_name='Sardinella longiceps' "
        "AND o.region='SE Arabian Sea' AND o.month BETWEEN 6 AND 9 GROUP BY f.year ORDER BY f.year"
    }

    async def run(self, args, ctx: ToolContext) -> ToolResult:
        sql = args.get("sql") or args.get("query")
        if not sql:
            raise ToolError("Missing 'sql'.")
        try:
            rows = ctx.store.query(str(sql))
        except QueryError as exc:
            raise ToolError(str(exc)) from exc
        rows = tidy(rows)
        truncated = len(rows) > ctx.settings.sql_row_limit
        rows = rows[: ctx.settings.sql_row_limit]
        sources = [dataset_source(d) for d in ctx.store.datasets_for_sql(str(sql))]
        if not rows:
            return ToolResult("(no rows) Check filter values with describe_dataset.", record=False)
        note = f"\n({len(rows)} rows{', truncated' if truncated else ''})"
        return ToolResult(format_rows(rows, note), data={"sql": sql, "rows": rows}, sources=sources)


class SpeciesLookup(Tool):
    name = "species_lookup"
    description = "Look up a species by scientific or common name: family, habitat, IUCN category, WoRMS AphiaID."
    example = {"name": "dugong"}

    async def run(self, args, ctx: ToolContext) -> ToolResult:
        name = str(args.get("name") or args.get("species") or "").strip()
        if not name:
            raise ToolError("Missing 'name'.")
        ds = ctx.store.dataset("species_reference")
        rows = tidy(ctx.store.query(
            "SELECT * FROM species_reference WHERE lower(scientific_name) LIKE ? OR lower(common_name) LIKE ?",
            (f"%{name.lower()}%", f"%{name.lower()}%"),
        ))
        if not rows:
            known = [r["scientific_name"] for r in ctx.store.query("SELECT scientific_name FROM species_reference")]
            return ToolResult(f"No species matching '{name}'. Known: {known}. Try obis_occurrences for other taxa.", record=False)
        return ToolResult(format_rows(rows), data={"rows": rows}, sources=[dataset_source(ds)])
