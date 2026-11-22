"""Evidence ledger: every data-bearing tool call becomes a numbered, citable record."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Source:
    kind: str  # dataset | obis | knowledge | calculation
    id: str
    title: str
    citation: str
    url: str | None = None
    synthetic: bool = False


@dataclass
class Evidence:
    id: str
    tool: str
    args: dict[str, Any]
    text: str  # what the agent saw as the observation body
    data: Any
    sources: list[Source]
    step_id: str | None = None
    numbers: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "args": self.args,
            "text": self.text,
            "data": self.data,
            "sources": [asdict(s) for s in self.sources],
            "step_id": self.step_id,
        }


def tidy(x: Any) -> Any:
    """Round floats once, so the agent, the ledger and the verifier all see the same value."""
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        if x == 0 or abs(x) >= 1:
            return round(x, 3)
        return float(f"{x:.4g}")
    if isinstance(x, dict):
        return {k: tidy(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [tidy(v) for v in x]
    return x


def collect_numbers(x: Any, out: list[float] | None = None) -> list[float]:
    out = [] if out is None else out
    if isinstance(x, bool):
        return out
    if isinstance(x, (int, float)):
        out.append(float(x))
    elif isinstance(x, dict):
        for v in x.values():
            collect_numbers(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            collect_numbers(v, out)
    return out


class EvidenceLedger:
    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}

    def add(
        self,
        tool: str,
        args: dict[str, Any],
        text: str,
        data: Any,
        sources: list[Source],
        step_id: str | None = None,
    ) -> Evidence:
        eid = f"E{len(self._items) + 1}"
        ev = Evidence(eid, tool, args, text, data, sources, step_id, collect_numbers(data))
        self._items[eid] = ev
        return ev

    def find(self, tool: str, args: dict[str, Any]) -> Evidence | None:
        """Return earlier evidence from an identical tool call, if any."""
        key = json.dumps(args, sort_keys=True, default=str)
        for ev in self._items.values():
            if ev.tool == tool and json.dumps(ev.args, sort_keys=True, default=str) == key:
                return ev
        return None

    def get(self, eid: str) -> Evidence | None:
        return self._items.get(eid)

    def all(self) -> list[Evidence]:
        return list(self._items.values())

    def all_numbers(self) -> list[float]:
        return [n for ev in self._items.values() for n in ev.numbers]

    def __len__(self) -> int:
        return len(self._items)
