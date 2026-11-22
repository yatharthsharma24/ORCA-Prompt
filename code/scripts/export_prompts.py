"""Write one prompt sheet per final prompt to prompts/final/, rendered from the code.

    python scripts/export_prompts.py [--runs ../test-data/outputs/run-v3-qwen3-8b]

System prompts are produced by the same functions the agents use, with
{variables} where per-run content goes. The sample run for each prompt is a
real LLM call recorded by scripts/run_tests.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orca.agents.react import REACT_FORMAT, ReActAgent  # noqa: E402
from orca.agents.roles import PLANNER_TOOLS, SPECIALISTS, Specialist, planner_prompt, specialist_prompt  # noqa: E402
from orca.agents.synthesizer import CRITIC_SCHEMA, CRITIC_SYSTEM, SYSTEM  # noqa: E402
from orca.config import Settings  # noqa: E402
from orca.orchestrator import PLAN_SCHEMA  # noqa: E402
from orca.tools import ALL_TOOLS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
S = Settings(_env_file=None)
MODEL = f"{S.model} via Ollama (local)"
BASE_SETTINGS = f"temperature {S.temperature}, num_ctx {S.num_ctx}, think=false"


def specialist_template() -> str:
    # Same structure as ReActAgent.system_prompt(), with variables in place of per-role content.
    role = specialist_prompt(Specialist("x", "{title}", "{focus}", ()), "{data_guide}")
    fmt = REACT_FORMAT.format(tool_names="{tool_names}", max_iter=S.max_react_iterations)
    return f"{role}\n\nTools:\n{{tool_descriptions}}\n\n{fmt}"


def planner_template() -> str:
    agent = ReActAgent("planner", planner_prompt("{data_guide}", S.max_plan_steps),
                       [ALL_TOOLS[t] for t in PLANNER_TOOLS], None, "m", max_iterations=2)
    return agent.system_prompt()


PROMPTS = [
    {
        "id": "P1", "file": "P1_planner.md", "name": "Planner (ReAct)", "key": "P1_planner",
        "step": "Step 1 of 4: question -> step plan",
        "purpose": "Break the user's question into 1-4 concrete steps, each assigned to one specialist agent.",
        "settings": BASE_SETTINGS + "; stop sequences ['\\nObservation:', 'Observation:']; at most 2 tool calls",
        "system": planner_template,
        "user": "Question: {question}\n\n(after each tool call the system adds a user turn)\nObservation: {observation}",
        "variables": {
            "question": "the user's question as typed",
            "data_guide": "generated list of datasets, columns, year ranges and valid filter values (from the SQLite catalog)",
            "observation": "tool result text, prefixed with its evidence id, e.g. [E1]",
        },
        "examples": "0 worked examples. Each tool line carries 1 example tool input (2 tools).",
        "output": 'Thought / Action / Action Input turns, then "Final Answer:" followed by JSON {"steps": [{"specialist": ..., "task": ...}]}',
    },
    {
        "id": "P2", "file": "P2_specialist.md", "name": "Specialist agent (ReAct; 4 roles)", "key": "P2_specialist",
        "step": "Step 2 of 4: run each plan step with tools",
        "purpose": "Do one plan step with data tools and return short findings with [E#] evidence citations.",
        "settings": BASE_SETTINGS + f"; stop sequences ['\\nObservation:', 'Observation:']; at most {S.max_react_iterations} tool calls; one nudge if it answers without using a tool",
        "system": specialist_template,
        "user": ("Overall question: {question}\n\nYour step ({step_id}): {task}\n"
                 "\nFindings from earlier steps (you may cite their evidence ids):\n{prior_findings}\n"
                 "\n(after each tool call)\nObservation: {observation}"),
        "variables": {
            "title / focus": "role name and focus; one of: " + "; ".join(f"{s.title} ({s.key})" for s in SPECIALISTS.values()),
            "tool_descriptions / tool_names": "the role's tool list with one-line descriptions and example inputs; roles get 2-9 tools",
            "data_guide": "same generated data guide as P1",
            "question": "the user's question",
            "step_id / task": "S1, S2, ... and the task text from the plan",
            "prior_findings": "findings of earlier steps (omitted for the first step)",
            "observation": "tool result with evidence id, or a tool error message",
        },
        "examples": "0 worked examples. 1 example input per tool; 1 example cited sentence in the format rules.",
        "output": 'Thought / Action / Action Input turns, then "Final Answer:" 2-5 sentences with [E#] citations',
    },
    {
        "id": "P3", "file": "P3_synthesizer.md", "name": "Synthesizer", "key": "P3_synthesizer",
        "step": "Step 3 of 4: write the cited answer",
        "purpose": "Turn the plan, findings and evidence records into one Markdown answer with [E#] citations.",
        "settings": BASE_SETTINGS,
        "system": lambda: SYSTEM,
        "user": ("Question: {question}\n\nPlan:\n{plan}\n\nSpecialist findings:\n{findings}\n\n"
                 "Evidence:\n{evidence}\n\nWrite the final answer now."),
        "variables": {
            "question": "the user's question",
            "plan": "numbered plan steps, e.g. '1. [fisheries] Correlate ...'",
            "findings": "each step's Final Answer",
            "evidence": "every evidence record: [E#] tool=..., source=..., then the tool output (up to 900 characters)",
        },
        "examples": "0 worked examples; 1 citation format example ([E2] or [E2, E4]).",
        "output": "Markdown: direct answer paragraph, **Key findings** bullets, **Caveats** bullets",
    },
    {
        "id": "P4", "file": "P4_revision.md", "name": "Revision after verification", "key": "P4_revision",
        "step": "Step 4 of 4 (only when checks fail): fix the answer",
        "purpose": "Rewrite the draft so every problem the verifier or critic found is fixed or the claim is removed.",
        "settings": BASE_SETTINGS + f"; at most {S.max_revisions} revision",
        "system": lambda: SYSTEM + "\n\n(same system prompt as P3; this is a follow-up turn in the same conversation)",
        "user": ("{P3 user prompt}\n[assistant] {draft}\n[user] The verifier checked your answer against the evidence "
                 "and found problems:\n{issues}\n\nRewrite the whole answer. For each problem either use the exact number "
                 "from the evidence with the correct [E#] citation, or remove the claim. Keep the same structure."),
        "variables": {
            "draft": "the P3 answer that failed a check",
            "issues": "one line per problem from the deterministic verifier and the critic (P5)",
        },
        "examples": "0",
        "output": "Same Markdown layout as P3",
    },
    {
        "id": "P5", "file": "P5_critic.md", "name": "Evidence critic", "key": "P5_critic",
        "step": "Step 4 of 4: check claims against evidence",
        "purpose": "Find claims a number check cannot catch: wrong site/year attached to a number, unsupported thresholds.",
        "settings": BASE_SETTINGS + "; structured output (Ollama format = JSON schema)",
        "system": lambda: CRITIC_SYSTEM,
        "user": "Question: {question}\n\nEvidence:\n{evidence}\n\nDraft answer:\n{answer}\n\nList the unsupported claims as JSON.",
        "variables": {
            "question": "the user's question",
            "evidence": "evidence records as in P3 (up to 1500 characters each)",
            "answer": "the synthesizer draft",
        },
        "examples": "0 worked examples; 1 inline comparison example (12.1 is above 12).",
        "output": "JSON " + json.dumps(CRITIC_SCHEMA),
    },
    {
        "id": "P6", "file": "P6_plan_repair.md", "name": "Plan repair (fallback)", "key": "P6_plan_repair",
        "step": "Step 1 fallback: only when the planner's Final Answer is not valid JSON",
        "purpose": "Convert a prose plan into the JSON plan format.",
        "settings": BASE_SETTINGS + "; structured output (Ollama format = JSON schema)",
        "system": lambda: ("Convert the plan below into JSON with a 'steps' list. Each step has 'specialist' "
                           f"(one of {list(SPECIALISTS)}) and 'task'."),
        "user": "Question: {question}\n\nPlan text:\n{planner_output}",
        "variables": {"question": "the user's question", "planner_output": "the planner's non-JSON Final Answer"},
        "examples": "0",
        "output": "JSON " + json.dumps(PLAN_SCHEMA),
    },
]


PREFERRED = ("T03", "T02", "T05", "T07")  # passing tests make clearer samples


def find_sample(runs_dir: Path, key: str) -> tuple[str, dict] | None:
    for dirpath in [runs_dir, *sorted(runs_dir.parent.glob("*"))]:
        files = sorted(Path(dirpath).glob("T*.json"), key=lambda p: (p.stem not in PREFERRED, PREFERRED.index(p.stem) if p.stem in PREFERRED else 0, p.stem))
        for p in files:
            data = json.loads(p.read_text(encoding="utf-8"))
            for call in data.get("llm_calls", []):
                if call["prompt_id"] == key:
                    return f"{Path(dirpath).name}/{p.name}", call
    return None


def render_sample(sample: tuple[str, dict] | None) -> str:
    if sample is None:
        return "Not triggered in any recorded test run (this fallback runs only when the planner's output is not valid JSON)."
    src, call = sample
    msgs = call["messages"]
    user_turns = [m for m in msgs if m["role"] != "system"]
    shown = user_turns if len(user_turns) <= 4 else user_turns[:1] + [{"role": "…", "content": f"({len(user_turns) - 3} earlier turns omitted)"}] + user_turns[-2:]
    parts = [f"Recorded in `test-data/outputs/{src}` · model {call['model']} · {call['seconds']} s\n",
             "**Input (messages after the system prompt):**\n"]
    for m in shown:
        parts.append(f"[{m['role']}]\n```text\n{m['content'][:3000]}\n```\n")
    parts.append(f"**Actual output:**\n```text\n{call['reply'][:3000]}\n```")
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, default=ROOT / "test-data" / "outputs" / "run-v3-qwen3-8b")
    ap.add_argument("--out", type=Path, default=ROOT / "prompts" / "final")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for p in PROMPTS:
        var_lines = "\n".join(f"- `{{{k}}}`: {v}" for k, v in p["variables"].items())
        text = f"""# {p['id']} · {p['name']}

| Field | Value |
|---|---|
| Prompt ID and name | {p['id']} · {p['name']} |
| Step in the workflow | {p['step']} |
| Purpose | {p['purpose']} |
| Tool and model | ORCA-Prompt (Python) · {MODEL} |
| Settings | {p['settings']} |
| Number of examples included | {p['examples']} |
| Output format | {p['output'].replace('|', '/')} |

## Full system prompt

```text
{p['system']()}
```

## Full user prompt template

```text
{p['user']}
```

## What each variable holds

{var_lines}

## Sample run (real input and actual output)

{render_sample(find_sample(args.runs, p['key']))}
"""
        (args.out / p["file"]).write_text(text, encoding="utf-8")
        print("wrote", args.out / p["file"])


if __name__ == "__main__":
    main()
