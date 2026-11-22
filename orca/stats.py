"""Small, dependency-free statistics used by the analysis tools."""

from __future__ import annotations

import math
from statistics import mean, pstdev


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Numerical Recipes)."""
    max_iter, eps, fpmin = 200, 3e-14, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > fpmin else fpmin)
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > fpmin else fpmin)
        c = 1.0 + aa / c
        c = c if abs(c) > fpmin else fpmin
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > fpmin else fpmin)
        c = 1.0 + aa / c
        c = c if abs(c) > fpmin else fpmin
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1 - x) / b


def t_two_sided_p(t: float, df: int) -> float:
    if df <= 0:
        return float("nan")
    return betainc(df / 2, 0.5, df / (df + t * t))


def normal_two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def ols(xs: list[float], ys: list[float]) -> dict[str, float]:
    n = len(xs)
    if n < 3:
        raise ValueError("need at least 3 points for a trend")
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    syy = sum((y - my) ** 2 for y in ys)
    slope = sxy / sxx
    intercept = my - slope * mx
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
    resid = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys, strict=True))
    se = math.sqrt(resid / (n - 2) / sxx) if n > 2 and sxx > 0 else float("nan")
    t = slope / se if se and se > 0 else float("inf")
    return {"slope": slope, "intercept": intercept, "r2": r2, "p_value": t_two_sided_p(t, n - 2), "n": n}


def mann_kendall(ys: list[float]) -> dict[str, float]:
    """Mann-Kendall monotonic trend test (normal approximation, tie-corrected)."""
    n = len(ys)
    s = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            s += (ys[j] > ys[i]) - (ys[j] < ys[i])
    counts: dict[float, int] = {}
    for y in ys:
        counts[y] = counts.get(y, 0) + 1
    var = (n * (n - 1) * (2 * n + 5) - sum(t * (t - 1) * (2 * t + 5) for t in counts.values())) / 18
    if s > 0:
        z = (s - 1) / math.sqrt(var)
    elif s < 0:
        z = (s + 1) / math.sqrt(var)
    else:
        z = 0.0
    return {"S": s, "z": z, "p_value": normal_two_sided_p(z)}


def sens_slope(xs: list[float], ys: list[float]) -> float:
    slopes = sorted(
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(len(xs) - 1)
        for j in range(i + 1, len(xs))
        if xs[j] != xs[i]
    )
    m = len(slopes)
    return slopes[m // 2] if m % 2 else (slopes[m // 2 - 1] + slopes[m // 2]) / 2


def pearson(xs: list[float], ys: list[float]) -> dict[str, float]:
    n = len(xs)
    if n < 4:
        raise ValueError("need at least 4 paired points for a correlation")
    mx, my = mean(xs), mean(ys)
    sx, sy = pstdev(xs), pstdev(ys)
    if sx == 0 or sy == 0:
        raise ValueError("one series is constant")
    r = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (n * sx * sy)
    r = max(-1.0, min(1.0, r))
    t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
    return {"r": r, "r2": r * r, "p_value": t_two_sided_p(t, n - 2), "n": n}


def _ranks(v: list[float]) -> list[float]:
    order = sorted(range(len(v)), key=lambda i: v[i])
    ranks = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> dict[str, float]:
    res = pearson(_ranks(xs), _ranks(ys))
    return {"rho": res["r"], "p_value": res["p_value"], "n": res["n"]}


def percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    if not s:
        raise ValueError("empty")
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)
