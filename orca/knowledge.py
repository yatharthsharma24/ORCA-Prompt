"""Tiny BM25 retriever over the markdown notes in data/knowledge."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = set(
    "a an and are as at be by for from has have in is it its of on or that the this to was were with "
    "which what how why when does do did can than then their there these those into over about".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass(frozen=True)
class Passage:
    doc_id: str
    passage_id: str
    title: str
    text: str
    source: str
    url: str | None


def _parse(path: Path) -> list[Passage]:
    raw = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = raw
    if raw.startswith("---"):
        _, front, body = raw.split("---", 2)
        for line in front.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    doc_id = meta.get("id", path.stem)
    paras = [p.strip() for p in body.strip().split("\n\n") if p.strip()]
    return [
        Passage(doc_id, f"{doc_id}#{i + 1}", meta.get("title", path.stem), p,
                meta.get("source", "ORCA knowledge note"), meta.get("url"))
        for i, p in enumerate(paras)
    ]


class KnowledgeBase:
    def __init__(self, directory: Path, k1: float = 1.4, b: float = 0.75) -> None:
        self.passages: list[Passage] = []
        if Path(directory).exists():
            for path in sorted(Path(directory).glob("*.md")):
                self.passages.extend(_parse(path))
        self.k1, self.b = k1, b
        self._docs = [tokenize(p.title + " " + p.text) for p in self.passages]
        self._avgdl = sum(map(len, self._docs)) / max(1, len(self._docs))
        df: dict[str, int] = {}
        for toks in self._docs:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, k: int = 3) -> list[tuple[Passage, float]]:
        q = tokenize(query)
        scored = []
        for p, toks in zip(self.passages, self._docs, strict=True):
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            score = 0.0
            for t in q:
                if t not in tf:
                    continue
                f = tf[t]
                score += self._idf.get(t, 0) * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * len(toks) / self._avgdl)
                )
            if score > 0:
                scored.append((p, score))
        scored.sort(key=lambda x: -x[1])
        return scored[:k]
