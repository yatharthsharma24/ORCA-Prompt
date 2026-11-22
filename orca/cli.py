"""Command line: python -m orca.cli ask "question"  |  python -m orca.cli check"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx

from orca.config import get_settings
from orca.data.store import MarineStore
from orca.knowledge import KnowledgeBase
from orca.llm import OllamaLLM
from orca.orchestrator import Orchestrator


def _print_event(kind: str, p: dict) -> None:
    if kind == "plan":
        print(f"\n== PLAN ({p['source']})")
        for i, s in enumerate(p["steps"], 1):
            print(f"  {i}. [{s['specialist']}] {s['task']}")
    elif kind == "step_started":
        print(f"\n== {p['step_id']} {p['title']}: {p['task']}")
    elif kind == "thought":
        print(f"  Thought ({p['agent']}): {p['text'][:300]}")
    elif kind == "action":
        print(f"  Action: {p['tool']} {json.dumps(p['input'])[:300]}")
    elif kind == "observation":
        tag = "ERROR " if p["error"] else ""
        print(f"  Observation {p['evidence_id'] or ''} {tag}{p['text'][:400]}")
    elif kind == "step_finished":
        print(f"  -> findings: {p['findings'][:600]}")
    elif kind == "verification":
        print(f"\n== VERIFY (rev {p['revision']}): passed={p['passed']} score={p['score']} "
              f"({p['verified_claims']}/{p['numeric_claims']})")
        for issue in p["issues"]:
            print(f"   ! {issue[:300]}")
        for c in p.get("critic_problems", []):
            print(f"   critic: {c['problem'][:200]} | {c['claim'][:150]}")
    elif kind == "phase":
        print(f"\n-- {p['phase']}")


async def _ask(question: str, as_json: bool) -> int:
    s = get_settings()
    llm = OllamaLLM(s.ollama_base_url, temperature=s.temperature, num_ctx=s.num_ctx, think=s.think,
                    timeout_s=s.llm_timeout_s, keep_alive=s.keep_alive)
    async with httpx.AsyncClient(timeout=s.obis_timeout_s) as http:
        orch = Orchestrator(s, llm, MarineStore(s.data_db, row_limit=s.sql_row_limit), KnowledgeBase(s.knowledge_dir), http)

        async def emit(kind: str, payload: dict) -> None:
            if not as_json:
                _print_event(kind, payload)

        result = await orch.run(question, emit)
    await llm.aclose()
    if as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print("\n== ANSWER\n" + result["answer_markdown"])
        print(f"\n({result['elapsed_s']} s, {len(result['evidence'])} evidence records)")
    return 0


async def _check() -> int:
    s = get_settings()
    llm = OllamaLLM(s.ollama_base_url)
    h = await llm.health(s.model)
    await llm.aclose()
    print(json.dumps(h, indent=2))
    return 0 if h["reachable"] and h["model_available"] else 1


def main() -> None:
    p = argparse.ArgumentParser(prog="orca")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="answer one question")
    a.add_argument("question")
    a.add_argument("--json", action="store_true")
    sub.add_parser("check", help="check Ollama and the configured model")
    args = p.parse_args()
    if args.cmd == "ask":
        sys.exit(asyncio.run(_ask(args.question, args.json)))
    sys.exit(asyncio.run(_check()))


if __name__ == "__main__":
    main()
