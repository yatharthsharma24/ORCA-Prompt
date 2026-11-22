"""Runs one question through planner -> specialists -> synthesizer -> verifier."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import asdict
from typing import Any

import httpx

from orca.agents.react import Emit, ReActAgent, extract_json_object
from orca.agents.roles import PLANNER_TOOLS, SPECIALISTS, data_guide, planner_prompt, specialist_prompt
from orca.agents.synthesizer import build_messages, critique, revision_messages, synthesize
from orca.agents.verifier import CITE_RE, verify
from orca.config import Settings
from orca.data.store import MarineStore
from orca.evidence import EvidenceLedger
from orca.knowledge import KnowledgeBase
from orca.llm import LLM, LLMError
from orca.tools import ALL_TOOLS, ToolContext

log = logging.getLogger(__name__)

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "specialist": {"type": "string", "enum": list(SPECIALISTS)},
                    "task": {"type": "string"},
                },
                "required": ["specialist", "task"],
            },
        }
    },
    "required": ["steps"],
}

_KEYWORDS = {
    "fisheries": ("landing", "catch", "fishery", "fisheries", "sardine", "mackerel", "tuna", "hilsa", "shrimp", "bombay duck", "tonnes"),
    "biodiversity": ("species", "dugong", "turtle", "ridley", "whale shark", "dolphin", "edna", "coral cover", "bleach", "reef", "occurrence", "obis", "iucn", "biodiversity"),
    "oceanography": ("sst", "temperature", "chlorophyll", "salinity", "oxygen", "upwelling", "heatwave", "warming", "anomal", "dhw", "monsoon"),
}


async def _noop(_type: str, _payload: dict[str, Any]) -> None:
    return None


def heuristic_plan(question: str) -> list[dict[str, str]]:
    q = question.lower()
    steps = [
        {"specialist": key, "task": f"Answer the part of this question about {SPECIALISTS[key].focus}: {question}"}
        for key, words in _KEYWORDS.items()
        if any(w in q for w in words)
    ]
    return steps or [{"specialist": "oceanography", "task": question}]


def normalise_plan(raw: Any, max_steps: int) -> list[dict[str, str]]:
    steps = raw.get("steps") if isinstance(raw, dict) else raw
    if not isinstance(steps, list):
        raise ValueError("plan has no 'steps' list")
    out = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        key = str(s.get("specialist", "")).lower().strip()
        key = {"ocean": "oceanography", "oceanographer": "oceanography", "fishery": "fisheries",
               "biology": "biodiversity", "literature": "context", "knowledge": "context"}.get(key, key)
        task = str(s.get("task") or s.get("goal") or "").strip()
        if key in SPECIALISTS and task:
            out.append({"specialist": key, "task": task})
    if not out:
        raise ValueError("plan has no valid steps")
    return out[:max_steps]


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        llm: LLM,
        store: MarineStore,
        kb: KnowledgeBase,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.store = store
        self.kb = kb
        self.http = http
        self.guide = data_guide(store)

    def _tools(self, names: tuple[str, ...]):
        return [ALL_TOOLS[n] for n in names if n != "obis_occurrences" or self.settings.enable_obis]

    async def _plan(self, question: str, ctx: ToolContext, emit: Emit) -> tuple[list[dict[str, str]], str]:
        s = self.settings
        planner = ReActAgent(
            "planner", planner_prompt(self.guide, s.max_plan_steps), self._tools(PLANNER_TOOLS),
            self.llm, s.model_for("planner"), max_iterations=2, observation_limit=s.observation_char_limit,
            final_instruction='Write "Final Answer:" followed by only the JSON plan {"steps": [...]}.',
        )
        ctx.step_id = "plan"
        res = await planner.run(f"Question: {question}", ctx, emit)
        try:
            return normalise_plan(extract_json_object(res.final_answer), s.max_plan_steps), "planner"
        except (ValueError, json.JSONDecodeError):
            log.info("planner output was not valid JSON; asking for structured repair")
        try:
            repair = await self.llm.chat(
                [
                    {"role": "system", "content": "Convert the plan below into JSON with a 'steps' list. "
                     f"Each step has 'specialist' (one of {list(SPECIALISTS)}) and 'task'."},
                    {"role": "user", "content": f"Question: {question}\n\nPlan text:\n{res.final_answer}"},
                ],
                model=s.model_for("planner"),
                json_schema=PLAN_SCHEMA,
            )
            return normalise_plan(json.loads(repair.text), s.max_plan_steps), "planner (repaired)"
        except (ValueError, json.JSONDecodeError):
            return heuristic_plan(question)[: s.max_plan_steps], "keyword fallback"

    async def _check(self, question: str, answer: str, ledger: EvidenceLedger, revision: int, emit: Emit):
        report = verify(answer, ledger)
        critic: list[dict[str, str]] = []
        if self.settings.llm_critic and ledger.all():
            await emit("phase", {"phase": "critic"})
            try:
                critic = await critique(self.llm, self.settings.model_for("synthesizer"), question, answer, ledger)
            except LLMError as exc:
                log.warning("critic failed: %s", exc)
        await emit("verification", {**report.to_dict(), "critic_problems": critic, "revision": revision})
        return report, critic

    async def run(self, question: str, emit: Emit = _noop, run_id: str | None = None) -> dict[str, Any]:
        s = self.settings
        run_id = run_id or uuid.uuid4().hex[:12]
        t_start = time.monotonic()
        ledger = EvidenceLedger()
        ctx = ToolContext(self.store, ledger, self.kb, s, self.http)
        await emit("run_started", {"run_id": run_id, "question": question, "model": s.model})

        await emit("phase", {"phase": "planning"})
        plan, plan_source = await self._plan(question, ctx, emit)
        await emit("plan", {"steps": plan, "source": plan_source})

        findings: list[dict[str, Any]] = []
        for i, step in enumerate(plan):
            spec = SPECIALISTS[step["specialist"]]
            step_id = f"S{i + 1}"
            ctx.step_id = step_id
            await emit("step_started", {"step_id": step_id, "specialist": spec.key, "title": spec.title, "task": step["task"]})
            agent = ReActAgent(
                spec.key, specialist_prompt(spec, self.guide), self._tools(spec.tools), self.llm,
                s.model_for("specialist"), max_iterations=s.max_react_iterations,
                observation_limit=s.observation_char_limit, require_tool=True,
            )
            prior = "\n".join(f"- {f['step_id']} ({f['specialist']}): {f['findings']}" for f in findings)
            task = (
                f"Overall question: {question}\n\nYour step ({step_id}): {step['task']}\n"
                + (f"\nFindings from earlier steps (you may cite their evidence ids):\n{prior}\n" if prior else "")
            )
            res = await agent.run(task, ctx, emit)
            findings_text, bad_cites = strip_invalid_citations(res.final_answer, {ev.id for ev in ledger.all()})
            record = {
                "step_id": step_id,
                "specialist": spec.key,
                "title": spec.title,
                "task": step["task"],
                "findings": findings_text,
                "invalid_citations_removed": bad_cites,
                "evidence_ids": res.evidence_ids,
                "forced_finish": res.forced,
                "trace": [asdict(st) for st in res.steps],
            }
            findings.append(record)
            await emit("step_finished", {k: v for k, v in record.items() if k != "trace"})

        await emit("phase", {"phase": "synthesis"})
        messages = build_messages(question, plan, findings, ledger)
        model = s.model_for("synthesizer")
        answer = await synthesize(self.llm, model, messages)
        await emit("draft", {"answer": answer, "revision": 0})
        report, critic = await self._check(question, answer, ledger, 0, emit)

        revisions = 0
        while (not report.passed or critic) and revisions < s.max_revisions:
            revisions += 1
            await emit("phase", {"phase": f"revision {revisions}"})
            issues = report.issues + [f"{p['problem']} -- claim: \"{p['claim']}\"" for p in critic]
            revised = await synthesize(self.llm, model, revision_messages(messages, answer, issues))
            await emit("draft", {"answer": revised, "revision": revisions})
            new_report, new_critic = await self._check(question, revised, ledger, revisions, emit)
            if (new_report.passed, -len(new_critic), new_report.score) >= (report.passed, -len(critic), report.score):
                answer, report, critic = revised, new_report, new_critic

        cited = report.cited_evidence or [ev.id for ev in ledger.all()]
        references = []
        for eid in cited:
            ev = ledger.get(eid)
            if ev:
                references.append({
                    "id": ev.id, "tool": ev.tool, "args": ev.args, "step_id": ev.step_id,
                    "sources": [asdict(src) for src in ev.sources],
                })
        uses_synthetic = any(src["synthetic"] for r in references for src in r["sources"])
        result = {
            "run_id": run_id,
            "question": question,
            "model": s.model,
            "plan": plan,
            "plan_source": plan_source,
            "steps": findings,
            "answer": answer,
            "answer_markdown": answer + "\n\n" + references_markdown(references, uses_synthetic),
            "references": references,
            "evidence": [ev.to_dict() for ev in ledger.all()],
            "verification": {**report.to_dict(), "critic_problems": critic, "critic_enabled": s.llm_critic},
            "revisions": revisions,
            "uses_synthetic_data": uses_synthetic,
            "elapsed_s": round(time.monotonic() - t_start, 1),
        }
        await emit("final", result)
        return result


def strip_invalid_citations(text: str, valid: set[str]) -> tuple[str, list[str]]:
    """Replace citations of evidence that does not exist with [unverified]."""
    bad: list[str] = []

    def fix(m: re.Match) -> str:
        ids = [i.strip() for i in re.split(r"[,;]", m.group(1))]
        keep = [i for i in ids if i in valid]
        bad.extend(i for i in ids if i not in valid)
        return f"[{', '.join(keep)}]" if keep else "[unverified]"

    return CITE_RE.sub(fix, text), bad


def references_markdown(references: list[dict[str, Any]], uses_synthetic: bool) -> str:
    lines = ["**References**"]
    for r in references:
        srcs = "; ".join(
            src["citation"] + (f" <{src['url']}>" if src.get("url") else "") for src in r["sources"]
        ) or "computed"
        lines.append(f"- [{r['id']}] {r['tool']} {json.dumps(r['args'], ensure_ascii=False)} -- {srcs}")
    if uses_synthetic:
        lines.append(
            "\n_Some cited evidence comes from ORCA's synthetic demo datasets. Replace them with real "
            "observations before using these numbers for decisions._"
        )
    return "\n".join(lines)
