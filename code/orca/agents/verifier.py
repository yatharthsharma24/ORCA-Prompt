"""Deterministic answer verifier.

Splits the answer into claims (sentences / bullets), pulls every number out of
each claim and checks it against the numbers recorded in the evidence ledger,
preferring the evidence the claim cites. No LLM is involved, so the check is
reproducible and explainable.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

from orca.evidence import EvidenceLedger

CITE_RE = re.compile(r"\[(E\d+(?:\s*[,;]\s*E\d+)*)\]")
_NUM_RE = re.compile(
    r"(?<![\w.\-/])([-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-−]?\d+(?:\.\d+)?(?:[eE][-+−]?\d+)?)"
    r"(\s*(?:%|percent|per\s?cent|thousand|lakh|million|k\b))?",
    re.I,
)
_DATE_RE = re.compile(r"\b(?:19|20)\d{2}-\d{2}(?:-\d{2})?\b")
_RANGE_YEARS = re.compile(r"\b(?:19|20)\d{2}\s*[-–to]+\s*(?:19|20)\d{2}\b")
_MULT = {"thousand": 1e3, "k": 1e3, "lakh": 1e5, "million": 1e6}
ALLOWED_CONSTANTS = {0.05, 0.01, 0.001, 0.1, 90.0, 95.0, 99.0}


@dataclass
class NumberCheck:
    text: str
    value: float
    matched_in: str | None  # evidence id or None


@dataclass
class Claim:
    text: str
    citations: list[str]
    numbers: list[NumberCheck] = field(default_factory=list)
    status: str = "no_numbers"  # verified | miscited | uncited | unsupported | invalid_citation | no_numbers


@dataclass
class VerificationReport:
    passed: bool
    score: float
    numeric_claims: int
    verified_claims: int
    claims: list[Claim]
    issues: list[str]
    cited_evidence: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _split_claims(answer: str) -> list[str]:
    out: list[str] = []
    for line in answer.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", line)
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", line)
        out.extend(p.strip() for p in parts if p.strip())
    return out


def _decimals(token: str) -> int:
    if "e" in token.lower():
        mant, exp = token.lower().split("e")
        mant_dec = len(mant.split(".")[1]) if "." in mant else 0
        return mant_dec - int(exp)
    return len(token.split(".")[1]) if "." in token else 0


def extract_numbers(sentence: str) -> list[tuple[str, float, int]]:
    """Return (raw_text, value, decimals) for each checkable number in a sentence."""
    s = CITE_RE.sub(" ", sentence)
    s = _DATE_RE.sub(" ", s)
    s = _RANGE_YEARS.sub(" ", s)
    s = re.sub(r"\b[A-Za-z]+\d+[A-Za-z\d]*\b", " ", s)  # identifiers such as E2, qwen3, CO2
    found = []
    for m in _NUM_RE.finditer(s):
        raw, suffix = m.group(1), (m.group(2) or "").strip().lower()
        token = raw.replace(",", "").replace("−", "-")
        try:
            value = float(token)
        except ValueError:
            continue
        dec = _decimals(token)
        if dec == 0 and value != 0:
            # "49,000" is a rounded figure: allow +/-500.
            digits = token.lstrip("-")
            dec = -(len(digits) - len(digits.rstrip("0")))
        if dec <= 0 and "." not in token and "e" not in token.lower() and not suffix:
            if 1900 <= abs(value) <= 2100:  # a year
                continue
            if abs(value) <= 12:  # small counts, months, list numbers
                continue
        mult = next((v for k, v in _MULT.items() if suffix.startswith(k)), 1.0)
        found.append((raw + (m.group(2) or ""), value * mult, dec if mult == 1.0 else dec - int(math.log10(mult))))
    return found


def _matches(value: float, decimals: int, candidates: list[float]) -> bool:
    a = abs(value)
    if a in ALLOWED_CONSTANTS:
        return True
    tol_abs = 0.5 * 10 ** (-decimals)
    for c in candidates:
        for scaled in (abs(c), abs(c) * 100, abs(c) / 100):
            if math.isclose(a, scaled, abs_tol=tol_abs + 1e-9) or math.isclose(a, scaled, rel_tol=0.005):
                return True
    return False


def _numbers_for(ledger: EvidenceLedger, eid: str) -> list[float]:
    ev = ledger.get(eid)
    if ev is None:
        return []
    nums = list(ev.numbers)
    if ev.tool == "search_knowledge":
        for _, v, _ in extract_numbers(ev.text):
            nums.append(v)
        # Knowledge notes legitimately mention small thresholds (DHW 4, 8, 12).
        nums += [float(x) for x in re.findall(r"\b\d+(?:\.\d+)?\b", ev.text)]
    return nums


def verify(answer: str, ledger: EvidenceLedger) -> VerificationReport:
    all_ids = [ev.id for ev in ledger.all()]
    all_nums = {eid: _numbers_for(ledger, eid) for eid in all_ids}
    claims: list[Claim] = []
    issues: list[str] = []
    cited: set[str] = set()

    for sentence in _split_claims(answer):
        cites = [c.strip() for grp in CITE_RE.findall(sentence) for c in re.split(r"[,;]", grp)]
        claim = Claim(text=sentence, citations=cites)
        bad = [c for c in cites if c not in all_nums]
        cited.update(c for c in cites if c in all_nums)
        for raw, value, dec in extract_numbers(sentence):
            where = next((c for c in cites if c in all_nums and _matches(value, dec, all_nums[c])), None)
            if where is None:
                where_any = next((e for e in all_ids if _matches(value, dec, all_nums[e])), None)
                claim.numbers.append(NumberCheck(raw, value, where_any and f"other:{where_any}"))
            else:
                claim.numbers.append(NumberCheck(raw, value, where))
        if bad:
            claim.status = "invalid_citation"
            issues.append(f"Cites evidence that does not exist ({', '.join(bad)}): \"{sentence}\"")
        elif claim.numbers:
            missing = [n for n in claim.numbers if n.matched_in is None]
            elsewhere = [n for n in claim.numbers if n.matched_in and n.matched_in.startswith("other:")]
            if missing:
                claim.status = "unsupported"
                issues.append(
                    f"Numbers {[n.text for n in missing]} are not in any evidence: \"{sentence}\""
                )
            elif elsewhere and not cites:
                claim.status = "uncited"
                ids = sorted({n.matched_in.split(':')[1] for n in elsewhere})
                issues.append(f"Numeric claim has no citation (numbers found in {', '.join(ids)}): \"{sentence}\"")
            elif elsewhere:
                claim.status = "miscited"
                ids = sorted({n.matched_in.split(':')[1] for n in elsewhere})
                issues.append(f"Numbers come from {', '.join(ids)}, not the cited {', '.join(cites)}: \"{sentence}\"")
            else:
                claim.status = "verified"
        claims.append(claim)

    numeric = [c for c in claims if c.numbers or c.status == "invalid_citation"]
    verified = [c for c in numeric if c.status == "verified"]
    hard_fail = any(c.status in ("unsupported", "invalid_citation") for c in claims)
    if not cited and ledger.all():
        issues.append("The answer cites no evidence at all.")
        hard_fail = True
    score = len(verified) / len(numeric) if numeric else 1.0
    return VerificationReport(
        passed=not hard_fail,
        score=round(score, 3),
        numeric_claims=len(numeric),
        verified_claims=len(verified),
        claims=claims,
        issues=issues,
        cited_evidence=sorted(cited, key=lambda e: int(e[1:])),
    )
