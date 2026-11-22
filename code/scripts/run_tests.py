"""Run the Milestone test table against a live local model and save every output.

    python scripts/run_tests.py --model qwen3:8b --out ../test-data/outputs/run-qwen3-8b

For each test this sends the question through the real FastAPI app (input
validation included), records every LLM call (prompt messages and the model's
reply), applies the automatic checks from test-data/inputs.json and writes:

    <out>/<TID>.json        full API result + recorded LLM calls
    <out>/summary.md        test table (input, type, expected, actual, pass/fail)
    <out>/summary.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from orca.api.app import create_app  # noqa: E402
from orca.config import Settings  # noqa: E402
from orca.llm import LLMResponse, OllamaLLM  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def prompt_id(messages: list[dict]) -> str:
    system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
    last = messages[-1]["content"] if messages else ""
    if system.startswith("You are the ORCA Planner"):
        return "P1_planner"
    if system.startswith("You are the ORCA Synthesizer"):
        return "P4_revision" if "The verifier checked your answer" in last else "P3_synthesizer"
    if system.startswith("You are the ORCA Evidence Critic"):
        return "P5_critic"
    if system.startswith("Convert the plan below into JSON"):
        return "P6_plan_repair"
    if system.startswith("You are the ORCA"):
        return "P2_specialist"
    return "unknown"


class RecordingLLM:
    """Wraps the real Ollama client and keeps a copy of every call."""

    def __init__(self, inner: OllamaLLM) -> None:
        self.inner = inner
        self.calls: list[dict] = []

    async def chat(self, messages, *, model, stop=None, json_schema=None) -> LLMResponse:
        t0 = time.monotonic()
        resp = await self.inner.chat(messages, model=model, stop=stop, json_schema=json_schema)
        self.calls.append({
            "prompt_id": prompt_id(messages), "model": model, "stop": stop, "json_schema": bool(json_schema),
            "messages": [dict(m) for m in messages], "reply": resp.text,
            "seconds": round(time.monotonic() - t0, 2),
            "prompt_tokens": resp.prompt_tokens, "completion_tokens": resp.completion_tokens,
        })
        return resp

    async def health(self, model):
        return await self.inner.health(model)


def check(test: dict, status: int, result: dict | None) -> tuple[bool, list[str]]:
    c = test["checks"]
    problems = []
    if status != c.get("status", 200):
        problems.append(f"HTTP {status}, expected {c.get('status', 200)}")
        return False, problems
    if result is None:
        return not problems, problems
    answer = result["answer"]
    if c.get("verification_passed") and not result["verification"]["passed"]:
        problems.append("verifier failed")
    if c.get("contains_any") and not any(s.lower() in answer.lower() for s in c["contains_any"]):
        problems.append(f"none of {c['contains_any']} in answer")
    for s in c.get("contains_all", []):
        if s.lower() not in answer.lower():
            problems.append(f"'{s}' missing")
    for s in c.get("not_contains", []):
        if s.lower() in answer.lower():
            problems.append(f"contains '{s}'")
    for pat in c.get("not_contains_regex", []):
        if re.search(pat, answer, re.I):
            problems.append(f"matches /{pat}/")
    return not problems, problems


def short(text: str, n: int = 160) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", nargs="*", help="test ids to run")
    ap.add_argument("--inputs", type=Path, default=ROOT / "test-data" / "inputs.json")
    ap.add_argument("--set", nargs="*", default=[], help="extra settings, e.g. llm_critic=false num_thread=2")
    args = ap.parse_args()

    tests = json.loads(args.inputs.read_text(encoding="utf-8"))
    if args.only:
        tests = [t for t in tests if t["id"] in args.only]
    extra = dict(kv.split("=", 1) for kv in args.set)
    settings = Settings(model=args.model, ollama_base_url=args.ollama, runs_db=args.out / "runs.sqlite",
                        max_queued_runs=100, _env_file=None, **extra)
    args.out.mkdir(parents=True, exist_ok=True)
    inner = OllamaLLM(args.ollama, temperature=settings.temperature, num_ctx=settings.num_ctx, think=settings.think,
                      timeout_s=settings.llm_timeout_s, num_thread=settings.num_thread)
    rec = RecordingLLM(inner)
    rows = []
    with TestClient(create_app(settings, rec)) as client:
        for t in tests:
            rec.calls.clear()
            t0 = time.monotonic()
            r = client.post("/api/ask?stream=false", json={"question": t["question"]})
            secs = round(time.monotonic() - t0, 1)
            result = r.json() if r.status_code == 200 else None
            ok, problems = check(t, r.status_code, result)
            if result is not None:
                actual = short(result["answer"].split("\n")[0], 220)
                v = result["verification"]
                actual += f" [verifier {'pass' if v['passed'] else 'FAIL'} {v['verified_claims']}/{v['numeric_claims']}]"
            else:
                actual = f"HTTP {r.status_code}: {short(json.dumps(r.json()), 120)}"
            (args.out / f"{t['id']}.json").write_text(json.dumps({
                "test": t, "status": r.status_code, "seconds": secs, "pass": ok, "problems": problems,
                "result": result if result is not None else r.json(), "llm_calls": list(rec.calls),
            }, indent=2, ensure_ascii=False), encoding="utf-8")
            rows.append({"id": t["id"], "type": t["type"], "question": t["question"], "expected": t["expected"],
                         "actual": actual, "pass": ok, "problems": problems, "seconds": secs,
                         "llm_calls": len(rec.calls)})
            print(f"{t['id']} {'PASS' if ok else 'FAIL'} {secs}s {problems}", flush=True)
    passed = sum(r["pass"] for r in rows)
    lines = [f"# Test run: {args.model} ({time.strftime('%Y-%m-%d %H:%M')})", "",
             f"Passed {passed}/{len(rows)}. Settings overrides: {extra or 'none'}.", "",
             "| Test no. | Input (short) | Type | Expected output | Actual output (short) | Pass / Fail |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        note = f" ({'; '.join(r['problems'])})" if r["problems"] else ""
        lines.append(f"| {r['id']} | {short(r['question'], 90)} | {r['type']} | {r['expected']} | "
                     f"{r['actual'].replace('|', '/')}{note} | {'Pass' if r['pass'] else 'Fail'} |")
    (args.out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{passed}/{len(rows)} passed -> {args.out / 'summary.md'}")


if __name__ == "__main__":
    main()
