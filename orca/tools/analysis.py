"""Statistical analysis tools: trend, correlation, anomalies, and a traced calculator."""

from __future__ import annotations

import ast
import math
import operator
import re
from statistics import mean, pstdev
from typing import Any

from orca import stats
from orca.data.store import Dataset, QueryError
from orca.evidence import Source, tidy
from orca.tools.base import Tool, ToolContext, ToolError, ToolResult
from orca.tools.data import build_where, check_column, dataset_source, resolve_dataset

SERIES_MAX_ROWS = 50_000

SERIES_DOC = (
    'a series spec {"dataset": id, "metric": column, "agg": "avg|sum|min|max", "where": {...}} '
    "aggregated to one value per year (metric is a plain column name; limit years with start_year/end_year)"
)


def _series(ctx: ToolContext, spec: dict[str, Any], by_month: bool = False) -> tuple[Dataset, list[tuple], str]:
    if not isinstance(spec, dict):
        raise ToolError(f"Expected {SERIES_DOC}.")
    ds = resolve_dataset(ctx.store, spec)
    metric = spec.get("metric") or spec.get("column")
    if not metric:
        raise ToolError("Missing 'metric' (a numeric column).")
    agg = str(spec.get("agg", "avg")).lower()
    m = re.match(r"^\s*(avg|sum|min|max)\s*\(\s*(\w+)\s*\)\s*$", str(metric), re.IGNORECASE)
    if m:  # accept "sum(col)" as metric
        agg, metric = m.group(1).lower(), m.group(2)
    check_column(ds, metric)
    if agg not in ("avg", "sum", "min", "max"):
        raise ToolError("'agg' must be avg, sum, min or max.")
    if "year" not in ds.columns:
        raise ToolError(f"{ds.table_name} has no 'year' column.")
    where_sql, params = build_where(ds, spec.get("where"))
    keys = '"year", "month"' if by_month else '"year"'
    if by_month and "month" not in ds.columns:
        raise ToolError(f"{ds.table_name} has no 'month' column; use mode 'annual'.")
    sql = (
        f'SELECT {keys}, {agg.upper()}("{metric}") AS v FROM "{ds.table_name}"{where_sql} '
        f"GROUP BY {keys} ORDER BY {keys}"
    )
    try:
        rows = ctx.store.query(sql, params, max_rows=SERIES_MAX_ROWS)
    except QueryError as exc:
        raise ToolError(str(exc)) from exc
    if len(rows) > SERIES_MAX_ROWS:
        raise ToolError(f"Series has more than {SERIES_MAX_ROWS} points; add filters.")
    lo, hi = spec.get("start_year"), spec.get("end_year")
    out = []
    for r in rows:
        if r["v"] is None:
            continue
        if lo is not None and r["year"] < int(lo):
            continue
        if hi is not None and r["year"] > int(hi):
            continue
        out.append((r["year"], r["month"], r["v"]) if by_month else (r["year"], r["v"]))
    if not out:
        raise ToolError(f"No data for that series. Check filter values with describe_dataset {ds.dataset_id}.")
    label = f"{agg}({metric}) in {ds.table_name}"
    if spec.get("where"):
        label += f" where {spec['where']}"
    return ds, out, label


def _split_year_filter(spec: dict[str, Any]) -> tuple[dict[str, Any], Any, str]:
    """Separate year filters from a series spec.

    For anomalies the baseline must come from the full series, so a year filter
    (or start_year/end_year) only limits which years are reported.
    """
    spec = dict(spec)
    where = dict(spec.get("where") or {})
    cond = where.pop("year", None)
    lo, hi = spec.pop("start_year", None), spec.pop("end_year", None)
    spec["where"] = where
    if cond is None and lo is None and hi is None:
        return spec, (lambda _y: True), ""
    if isinstance(cond, list):
        years = {int(y) for y in cond}
        if len(years) == 2 and max(years) - min(years) > 1:
            # Two far-apart years are almost always meant as a range.
            lo, hi = min(years), max(years)
        else:
            return spec, (lambda y: y in years), f"years {sorted(years)}"
    elif isinstance(cond, dict):
        for op, val in cond.items():
            op = str(op).lower()
            if op == "between":
                lo, hi = int(val[0]), int(val[1])
            elif op in (">=", ">"):
                lo = int(val) + (op == ">")
            elif op in ("<=", "<"):
                hi = int(val) - (op == "<")
    elif cond is not None:
        lo = hi = int(cond)
    lo_i = int(lo) if lo is not None else -10**9
    hi_i = int(hi) if hi is not None else 10**9
    desc = f"years {lo if lo is not None else '...'}-{hi if hi is not None else '...'}"
    return spec, (lambda y: lo_i <= y <= hi_i), desc


class Trend(Tool):
    name = "trend"
    description = (
        "Fit a yearly trend to " + SERIES_DOC + ". Returns OLS slope per year and per decade, r2, p-value, "
        "Mann-Kendall test and Sen's slope. Optional start_year/end_year."
    )
    example = {"dataset": "D-OCEAN", "metric": "sst_c", "agg": "avg", "where": {"region": "Lakshadweep Sea"}}

    async def run(self, args, ctx: ToolContext) -> ToolResult:
        ds, series, label = _series(ctx, args)
        xs = [float(y) for y, _ in series]
        ys = [float(v) for _, v in series]
        try:
            fit = stats.ols(xs, ys)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        mk = stats.mann_kendall(ys)
        imin, imax = ys.index(min(ys)), ys.index(max(ys))
        data = tidy({
            "series": label,
            "years": f"{int(xs[0])}-{int(xs[-1])}",
            "n_years": len(xs),
            "slope_per_year": fit["slope"],
            "slope_per_decade": fit["slope"] * 10,
            "r2": fit["r2"],
            "ols_p_value": fit["p_value"],
            "mann_kendall_S": mk["S"],
            "mann_kendall_z": mk["z"],
            "mann_kendall_p_value": mk["p_value"],
            "sens_slope_per_year": stats.sens_slope(xs, ys),
            "first_value": ys[0],
            "last_value": ys[-1],
            "mean_value": mean(ys),
            "min_value": ys[imin], "min_year": int(xs[imin]),
            "max_value": ys[imax], "max_year": int(xs[imax]),
            "values_by_year": {int(x): y for x, y in zip(xs, ys, strict=True)},
        })
        sig = "significant at 0.05" if data["mann_kendall_p_value"] < 0.05 else "not significant at 0.05"
        text = (
            f"Trend of {label}, {data['years']} (n={data['n_years']} years)\n"
            f"OLS slope {data['slope_per_year']} per year = {data['slope_per_decade']} per decade; "
            f"r2={data['r2']}, p={data['ols_p_value']}\n"
            f"Mann-Kendall S={data['mann_kendall_S']}, z={data['mann_kendall_z']}, "
            f"p={data['mann_kendall_p_value']} ({sig}); Sen's slope {data['sens_slope_per_year']} per year\n"
            f"First {data['first_value']}, last {data['last_value']}, mean {data['mean_value']}, "
            f"min {data['min_value']} ({data['min_year']}), max {data['max_value']} ({data['max_year']})\n"
            f"Values: {data['values_by_year']}"
        )
        return ToolResult(text, data=data, sources=[dataset_source(ds)])


class Correlate(Tool):
    name = "correlate"
    description = (
        "Correlate two yearly series. 'x' and 'y' are each " + SERIES_DOC + ". "
        "'lag_years' pairs x in year (t - lag) with y in year t. Returns Pearson r, Spearman rho, p-values, n."
    )
    example = {
        "x": {"dataset": "D-OCEAN", "metric": "chlorophyll_mg_m3", "agg": "avg",
              "where": {"region": "SE Arabian Sea", "month": [6, 7, 8, 9]}},
        "y": {"dataset": "D-FISH", "metric": "landings_tonnes", "agg": "sum",
              "where": {"state": "Kerala", "scientific_name": "Sardinella longiceps"}},
        "lag_years": 1,
    }

    async def run(self, args, ctx: ToolContext) -> ToolResult:
        if "x" not in args or "y" not in args:
            raise ToolError("Need both 'x' and 'y' series specs.")
        lag = int(args.get("lag_years", 0))
        dx, sx, lx = _series(ctx, args["x"])
        dy, sy, ly = _series(ctx, args["y"])
        xmap = dict(sx)
        pairs = [(year, xmap[year - lag], v) for year, v in sy if (year - lag) in xmap]
        if len(pairs) < 4:
            raise ToolError(f"Only {len(pairs)} overlapping years; need at least 4.")
        xs = [float(p[1]) for p in pairs]
        ys = [float(p[2]) for p in pairs]
        try:
            pr, sp = stats.pearson(xs, ys), stats.spearman(xs, ys)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        data = tidy({
            "x": lx, "y": ly, "lag_years": lag, "n_pairs": len(pairs),
            "pearson_r": pr["r"], "r2": pr["r2"], "pearson_p_value": pr["p_value"],
            "spearman_rho": sp["rho"], "spearman_p_value": sp["p_value"],
            "pairs": [{"y_year": y, "x": a, "y": b} for y, a, b in pairs],
        })
        strength = abs(data["pearson_r"])
        word = "strong" if strength >= 0.7 else "moderate" if strength >= 0.4 else "weak"
        text = (
            f"x = {lx}\ny = {ly}\nlag = {lag} year(s) (x in year t-{lag} vs y in year t), n={data['n_pairs']}\n"
            f"Pearson r={data['pearson_r']} (r2={data['r2']}, p={data['pearson_p_value']}) -> {word} "
            f"{'positive' if data['pearson_r'] > 0 else 'negative'} association\n"
            f"Spearman rho={data['spearman_rho']} (p={data['spearman_p_value']})\n"
            "Correlation is not causation; consider confounders and the short record.\n"
            f"Pairs (y_year: x, y): " + ", ".join(f"{p['y_year']}: {p['x']}, {p['y']}" for p in data["pairs"])
        )
        sources = [dataset_source(dx)] + ([dataset_source(dy)] if dy.dataset_id != dx.dataset_id else [])
        return ToolResult(text, data=data, sources=sources)


class Anomaly(Tool):
    name = "anomaly"
    description = (
        "Compare values with a baseline climatology. mode 'annual' (default): yearly anomalies and z-scores "
        "for " + SERIES_DOC + ". mode 'monthly': flags months above the baseline 90th percentile of that "
        "calendar month (a monthly marine-heatwave approximation). 'baseline' is [start_year, end_year] and "
        "is always taken from the full series; a year filter in 'where' only limits which years are reported."
    )
    example = {"dataset": "D-OCEAN", "metric": "sst_c", "agg": "avg", "where": {"region": "Andaman Sea"},
               "baseline": [2004, 2013], "mode": "monthly"}

    async def run(self, args, ctx: ToolContext) -> ToolResult:
        mode = str(args.get("mode", "annual")).lower()
        base = args.get("baseline") or [2004, 2013]
        try:
            b0, b1 = int(base[0]), int(base[1])
        except (TypeError, ValueError, IndexError) as exc:
            raise ToolError("'baseline' must be [start_year, end_year].") from exc
        spec, report_year, window = _split_year_filter(args)
        if mode == "monthly":
            return self._monthly(ctx, spec, b0, b1, report_year, window)
        ds, series, label = _series(ctx, spec)
        bvals = [v for y, v in series if b0 <= y <= b1]
        if len(bvals) < 3:
            years = [y for y, _ in series]
            raise ToolError(f"Baseline {b0}-{b1} has fewer than 3 years; data covers {min(years)}-{max(years)}.")
        mu, sd = mean(bvals), pstdev(bvals) or float("nan")
        rows = [{"year": y, "value": v, "anomaly": v - mu, "z": (v - mu) / sd} for y, v in series if report_year(y)]
        if not rows:
            raise ToolError("No years left after the year filter.")
        if window:
            label += f", reporting {window}"
        ranked = sorted(rows, key=lambda r: -r["anomaly"])
        data = tidy({
            "series": label, "baseline": f"{b0}-{b1}", "baseline_mean": mu, "baseline_sd": sd,
            "years": rows, "largest_positive": ranked[:3], "largest_negative": ranked[-3:][::-1],
        })
        fmt = lambda r: f"{r['year']}: {r['value']} (anom {r['anomaly']}, z {r['z']})"  # noqa: E731
        text = (
            f"Annual anomalies of {label} vs baseline {data['baseline']} "
            f"(mean {data['baseline_mean']}, sd {data['baseline_sd']})\n"
            "Largest positive: " + "; ".join(map(fmt, data["largest_positive"])) + "\n"
            "Largest negative: " + "; ".join(map(fmt, data["largest_negative"])) + "\n"
            "All years: " + "; ".join(f"{r['year']}: {r['anomaly']}" for r in data["years"])
        )
        return ToolResult(text, data=data, sources=[dataset_source(ds)])

    def _monthly(self, ctx: ToolContext, args, b0: int, b1: int, report_year=lambda _y: True, window: str = "") -> ToolResult:
        ds, series, label = _series(ctx, args, by_month=True)
        if window:
            label += f", reporting {window}"
        clim: dict[int, list[float]] = {}
        for y, m, v in series:
            if b0 <= y <= b1:
                clim.setdefault(m, []).append(v)
        months_present = {m for _, m, _ in series}
        thin = sorted(m for m in months_present if len(clim.get(m, [])) < 3)
        if thin:
            raise ToolError(
                f"Baseline {b0}-{b1} has fewer than 3 years for month(s) {thin}. "
                "Widen the baseline or use mode 'annual'."
            )
        p90 = {m: stats.percentile(v, 0.9) for m, v in clim.items()}
        mu = {m: mean(v) for m, v in clim.items()}
        flagged = [
            {"year": y, "month": m, "value": v, "threshold_p90": p90[m], "anomaly": v - mu[m],
             "category": _mhw_category(v, mu[m], p90[m])}
            for y, m, v in series if m in p90 and v > p90[m] and not (b0 <= y <= b1) and report_year(y)
        ]
        per_year: dict[int, int] = {}
        for f in flagged:
            per_year[f["year"]] = per_year.get(f["year"], 0) + 1
        data = tidy({
            "series": label, "baseline": f"{b0}-{b1}", "method": "monthly 90th-percentile exceedance (approximation)",
            "n_flagged_months": len(flagged), "flagged_months_per_year": per_year,
            "flagged_months": [f"{f['year']}-{f['month']:02d}" for f in flagged],
            "top_flagged": sorted(flagged, key=lambda f: -f["anomaly"])[:8],
        })
        text = (
            f"Monthly exceedances of {label} above the {data['baseline']} 90th percentile "
            f"(monthly approximation of the Hobday et al. 2016 marine heatwave threshold)\n"
            f"Flagged months after baseline: {data['n_flagged_months']}\n"
            f"Flagged months per year: {data['flagged_months_per_year']}\n"
            f"All flagged months: {', '.join(data['flagged_months']) or 'none'}\n"
            "Largest: " + "; ".join(
                f"{f['year']}-{f['month']:02d}: {f['value']} (p90 {f['threshold_p90']}, anom {f['anomaly']}, {f['category']})"
                for f in data["top_flagged"]
            )
        )
        return ToolResult(text, data=data, sources=[dataset_source(ds)])


def _mhw_category(v: float, mu: float, p90: float) -> str:
    step = p90 - mu
    if step <= 0:
        return "moderate"
    k = (v - mu) / step
    return "extreme" if k >= 4 else "severe" if k >= 3 else "strong" if k >= 2 else "moderate"


_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod}
_FUNCS = {"abs": abs, "round": round, "min": min, "max": max, "sqrt": math.sqrt, "log": math.log,
          "log10": math.log10, "exp": math.exp, "mean": lambda *a: mean(a)}


def safe_eval(expr: str) -> float:
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ToolError("Exponent too large.")
            return _BIN[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return -ev(node.operand) if isinstance(node.op, ast.USub) else ev(node.operand)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ToolError("Only numbers, + - * / ** %, parentheses and abs/round/min/max/sqrt/log/log10/exp/mean are allowed.")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"Cannot parse expression: {exc.msg}") from exc
    try:
        return float(ev(tree))
    except ZeroDivisionError as exc:
        raise ToolError("Division by zero.") from exc


def _literals(expr: str) -> list[float]:
    out = []
    for node in ast.walk(ast.parse(expr, mode="eval")):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            out.append(float(node.value))
    return out


class Calculate(Tool):
    name = "calculate"
    description = (
        "Evaluate arithmetic on numbers taken from earlier observations (percent change, ratios, differences). "
        "Every derived number you report must come from this tool or another tool. Do not use thousands separators."
    )
    example = {"expression": "(48670 - 390830) / 390830 * 100", "label": "percent change in Kerala oil sardine landings 2013 to 2016"}

    async def run(self, args, ctx: ToolContext) -> ToolResult:
        expr = str(args.get("expression") or "").strip()
        if not expr:
            raise ToolError("Missing 'expression'.")
        result = safe_eval(expr)
        known = ctx.ledger.all_numbers()
        untraced = [
            n for n in _literals(expr)
            if n not in (0, 1, 2, 10, 100, 1000) and not any(math.isclose(n, k, rel_tol=1e-3, abs_tol=1e-9) for k in known)
        ]
        data = tidy({"expression": expr, "label": str(args.get("label", "")), "result": result})
        text = f"{data['label'] or expr} = {data['result']}  (expression: {expr})"
        src = Source("calculation", "calc", "ORCA calculator", f"Computed: {expr}")
        if untraced:
            # The result is not grounded in data, so it must not become verifiable evidence.
            shown = ", ".join(f"{n:g}" for n in untraced)
            return ToolResult(
                text + f"\nREJECTED: inputs {shown} do not appear in any earlier observation. "
                "Use numbers exactly as the data tools reported them.",
                record=False,
            )
        return ToolResult(text, data=data, sources=[src])
