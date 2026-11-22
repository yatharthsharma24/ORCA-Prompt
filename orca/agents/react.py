"""Text-format ReAct loop (Thought / Action / Action Input / Observation).

Plain-text ReAct works with any local model, including ones without native
tool calling, and keeps the reasoning trace readable in the UI.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from orca.llm import LLM
from orca.tools import Tool, ToolContext, ToolError

log = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

_FINAL = re.compile(r"Final Answer\s*:\s*(.*)", re.S | re.I)
_ACTION = re.compile(r"Action\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?", re.I)
_ACTION_INPUT = re.compile(r"Action Input\s*:\s*(.*)", re.S | re.I)
_THOUGHT = re.compile(r"Thought\s*:\s*(.*?)(?=\n\s*(?:Action|Final Answer)\s*:|\Z)", re.S | re.I)

REACT_FORMAT = """Use exactly this format, one tool call per turn:

Thought: what you need to find out next and why
Action: the tool name, one of [{tool_names}]
Action Input: a single JSON object with the tool's arguments

Then stop. The system replies with "Observation: [E#] ..." where E# is an evidence id.
Repeat until you have what you need (at most {max_iter} tool calls), then write:

Thought: I have enough evidence
Final Answer: your findings, citing evidence ids in square brackets after each claim, e.g. "Mean SST rose 0.26 degC per decade [E2]."

Rules:
- Every number in the Final Answer must appear in an observation. For derived numbers (percent change, differences) call the calculate tool first.
- Never invent data, sources or evidence ids. If the data cannot answer, say so plainly.
- Mention when a cited dataset is SYNTHETIC demo data."""


@dataclass
class Step:
    thought: str = ""
    action: str | None = None
    action_input: dict[str, Any] | None = None
    observation: str | None = None
    evidence_id: str | None = None
    error: bool = False


@dataclass
class ReActResult:
    final_answer: str
    steps: list[Step] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    forced: bool = False
    llm_ms: int = 0
    tokens: int = 0


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    start = text.find("{")
    if start == -1:
        if text.strip() in ("", "{}", "none", "None"):
            return {}
        raise ValueError("no JSON object found")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("Action Input must be a JSON object")
    return obj


def parse_reply(text: str) -> tuple[str, str | None, str | None, str | None]:
    """Return (thought, final_answer, action, action_input_raw)."""
    thought_m = _THOUGHT.search(text)
    thought = thought_m.group(1).strip() if thought_m else ""
    final_m = _FINAL.search(text)
    action_m = _ACTION.search(text)
    if final_m and (not action_m or final_m.start() < action_m.start()):
        return thought, final_m.group(1).strip(), None, None
    if action_m:
        input_m = _ACTION_INPUT.search(text, action_m.end())
        return thought, None, action_m.group(1), input_m.group(1) if input_m else ""
    if not thought:
        thought = text.strip()
    return thought, None, None, None


class ReActAgent:
    def __init__(
        self,
        name: str,
        role_prompt: str,
        tools: list[Tool],
        llm: LLM,
        model: str,
        *,
        max_iterations: int = 6,
        observation_limit: int = 2500,
        final_instruction: str = "Now write 'Thought:' and 'Final Answer:' using only the observations above.",
        require_tool: bool = False,
    ) -> None:
        self.final_instruction = final_instruction
        self.require_tool = require_tool
        self.name = name
        self.role_prompt = role_prompt
        self.tools = {t.name: t for t in tools}
        self.llm = llm
        self.model = model
        self.max_iterations = max_iterations
        self.observation_limit = observation_limit

    def system_prompt(self) -> str:
        tool_lines = "\n".join(t.prompt_line() for t in self.tools.values())
        fmt = REACT_FORMAT.format(tool_names=", ".join(self.tools), max_iter=self.max_iterations)
        return f"{self.role_prompt}\n\nTools:\n{tool_lines}\n\n{fmt}"

    async def run(self, task: str, ctx: ToolContext, emit: Emit) -> ReActResult:
        messages = [{"role": "system", "content": self.system_prompt()}, {"role": "user", "content": task}]
        result = ReActResult(final_answer="")
        tool_calls = 0
        format_errors = 0
        failed_calls: set[str] = set()
        nudged = False
        while True:
            t0 = time.monotonic()
            resp = await self.llm.chat(messages, model=self.model, stop=["\nObservation:", "Observation:"])
            result.llm_ms += int((time.monotonic() - t0) * 1000)
            result.tokens += resp.prompt_tokens + resp.completion_tokens
            reply = resp.text.strip()
            thought, final, action, raw_input = parse_reply(reply)
            step = Step(thought=thought)

            if final is not None and self.require_tool and tool_calls == 0 and not nudged:
                # Specialists tend to copy earlier steps' findings instead of doing their own step.
                nudged = True
                messages += [
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content": "Observation: You have not used any tool for your step yet. "
                     "Earlier findings are context, not evidence for your step. Call a tool now to get "
                     "evidence for your step. Only if an earlier evidence id already contains exactly what "
                     "your step asks for may you answer directly, citing that id."},
                ]
                continue

            if final is not None:
                if thought:
                    await emit("thought", {"agent": self.name, "text": thought})
                result.final_answer = final
                result.steps.append(step)
                return result

            if action is None:
                format_errors += 1
                if format_errors >= 2:
                    # The model is answering in prose; accept it rather than loop.
                    result.final_answer = reply
                    result.forced = True
                    return result
                messages += [
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content": "Observation: Format error. Reply with either "
                     "'Thought/Action/Action Input' or 'Thought/Final Answer'."},
                ]
                continue

            await emit("thought", {"agent": self.name, "text": thought})
            step.action = action
            obs, eid, is_error = await self._call_tool(action, raw_input or "", ctx, step)
            if is_error:
                sig = f"{action}:{json.dumps(step.action_input, sort_keys=True) if step.action_input else raw_input}"
                if sig in failed_calls:
                    obs += ("\nYou already made this exact call and it failed. Change the input, "
                            "use a different tool, or give your Final Answer.")
                failed_calls.add(sig)
            step.observation, step.evidence_id, step.error = obs, eid, is_error
            result.steps.append(step)
            if eid and eid not in result.evidence_ids:
                result.evidence_ids.append(eid)
            await emit("action", {"agent": self.name, "tool": action, "input": step.action_input or raw_input})
            await emit("observation", {"agent": self.name, "tool": action, "evidence_id": eid,
                                       "text": obs, "error": is_error})
            tool_calls += 1
            clean_reply = reply.split("Observation:")[0].strip()
            messages += [
                {"role": "assistant", "content": clean_reply},
                {"role": "user", "content": f"Observation: {obs}"},
            ]
            if tool_calls >= self.max_iterations:
                messages.append({
                    "role": "user",
                    "content": f"You have used all your tool calls. {self.final_instruction}",
                })
                resp = await self.llm.chat(messages, model=self.model, stop=["\nObservation:", "Observation:"])
                result.tokens += resp.prompt_tokens + resp.completion_tokens
                _, final, _, _ = parse_reply(resp.text)
                result.final_answer = final or resp.text.strip()
                result.forced = True
                return result

    async def _call_tool(self, action: str, raw: str, ctx: ToolContext, step: Step) -> tuple[str, str | None, bool]:
        tool = self.tools.get(action)
        if tool is None:
            return f"Unknown tool '{action}'. Available: {', '.join(self.tools)}.", None, True
        try:
            args = extract_json_object(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            return f"Action Input is not valid JSON ({exc}). Example: {json.dumps(tool.example)}", None, True
        step.action_input = args
        earlier = ctx.ledger.find(action, args)
        if earlier is not None:
            return f"[{earlier.id}] (same call as before) {earlier.text}", earlier.id, False
        try:
            res = await tool.run(args, ctx)
        except ToolError as exc:
            return f"Tool error: {exc}", None, True
        except Exception as exc:  # noqa: BLE001 - surface unexpected tool failures to the agent
            log.exception("tool %s crashed", action)
            return f"Tool failed unexpectedly: {type(exc).__name__}: {exc}", None, True
        text = res.text
        if len(text) > self.observation_limit:
            text = text[: self.observation_limit] + "\n...(truncated)"
        if not res.record:
            return text, None, False
        ev = ctx.ledger.add(action, args, text, res.data, res.sources, ctx.step_id)
        synthetic = any(s.synthetic for s in res.sources)
        prefix = f"[{ev.id}]" + (" (SYNTHETIC demo data)" if synthetic else "")
        return f"{prefix} {text}", ev.id, False
