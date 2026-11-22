import json

import pytest

from orca.agents.react import ReActAgent, extract_json_object, parse_reply
from orca.llm import ScriptedLLM
from orca.orchestrator import Orchestrator, heuristic_plan, normalise_plan, strip_invalid_citations
from orca.tools import ALL_TOOLS


async def _collect(events):
    async def emit(kind, payload):
        events.append((kind, payload))
    return emit


def test_parse_reply_variants():
    t, final, action, raw = parse_reply('Thought: need data\nAction: trend\nAction Input: {"dataset": "D-OCEAN"}')
    assert (t, final, action) == ("need data", None, "trend")
    assert extract_json_object(raw) == {"dataset": "D-OCEAN"}
    _, final, action, _ = parse_reply("Thought: done\nFinal Answer: SST rose [E1].")
    assert final == "SST rose [E1]." and action is None
    _, _, action, raw = parse_reply('Action: `query_data`\nAction Input: ```json\n{"a": 1}\n```')
    assert action == "query_data" and extract_json_object(raw) == {"a": 1}


def test_plan_normalisation():
    plan = normalise_plan({"steps": [{"specialist": "Fishery", "task": "t1"}, {"specialist": "nope", "task": "x"}]}, 4)
    assert plan == [{"specialist": "fisheries", "task": "t1"}]
    with pytest.raises(ValueError):
        normalise_plan({"steps": []}, 4)
    assert heuristic_plan("oil sardine landings and SST")[0]["specialist"] == "fisheries"


def test_strip_invalid_citations():
    text, bad = strip_invalid_citations("A [E1]. B [E9]. C [E1, E5].", {"E1"})
    assert text == "A [E1]. B [unverified]. C [E1]."
    assert bad == ["E9", "E5"]


async def test_react_loop_records_evidence(ctx):
    llm = ScriptedLLM([
        'Thought: get trend\nAction: trend\nAction Input: {"dataset": "D-OCEAN", "metric": "sst_c", "where": {"region": "Andaman Sea"}}',
        "Thought: done\nFinal Answer: Andaman SST warmed [E1].",
    ])
    agent = ReActAgent("oceanography", "role", [ALL_TOOLS["trend"]], llm, "m")
    events = []
    res = await agent.run("task", ctx, await _collect(events))
    assert res.final_answer == "Andaman SST warmed [E1]."
    assert res.evidence_ids == ["E1"]
    assert llm.calls[0]["stop"]  # stops before a hallucinated Observation
    kinds = [k for k, _ in events]
    assert kinds == ["thought", "action", "observation", "thought"]


async def test_react_handles_bad_json_and_repeated_errors(ctx):
    llm = ScriptedLLM([
        "Thought: x\nAction: trend\nAction Input: {not json}",
        'Thought: x\nAction: trend\nAction Input: {"dataset": "nope", "metric": "sst_c"}',
        'Thought: x\nAction: trend\nAction Input: {"dataset": "nope", "metric": "sst_c"}',
        "Thought: give up\nFinal Answer: The data could not be retrieved.",
    ])
    agent = ReActAgent("oceanography", "role", [ALL_TOOLS["trend"]], llm, "m", max_iterations=5)
    res = await agent.run("task", ctx, await _collect([]))
    assert res.steps[0].error and "not valid JSON" in res.steps[0].observation
    assert "already made this exact call" in res.steps[2].observation
    assert res.evidence_ids == []


async def test_react_forces_final_after_budget(ctx):
    call = 'Thought: more\nAction: search_knowledge\nAction Input: {"query": "upwelling %d"}'
    llm = ScriptedLLM([call % 1, call % 2, "Final Answer: forced [E1]."])
    agent = ReActAgent("context", "role", [ALL_TOOLS["search_knowledge"]], llm, "m", max_iterations=2)
    res = await agent.run("task", ctx, await _collect([]))
    assert res.forced and res.final_answer == "forced [E1]."


async def test_specialist_is_nudged_to_use_a_tool(ctx):
    llm = ScriptedLLM([
        "Thought: earlier step covered it\nFinal Answer: copied findings [E1].",
        'Thought: fine\nAction: species_lookup\nAction Input: {"name": "olive ridley"}',
        "Thought: done\nFinal Answer: Olive ridley is VU [E1].",
    ])
    agent = ReActAgent("biodiversity", "role", [ALL_TOOLS["species_lookup"]], llm, "m", require_tool=True)
    res = await agent.run("task", ctx, await _collect([]))
    assert res.evidence_ids == ["E1"]
    assert "not used any tool" in llm.calls[1]["messages"][-1]["content"]


async def test_orchestrator_end_to_end_with_revision(settings, store, kb):
    corr = json.dumps(ALL_TOOLS["correlate"].example)
    llm = ScriptedLLM([
        # planner
        'Thought: one fisheries step\nFinal Answer: {"steps": [{"specialist": "fisheries", "task": "correlate upwelling and landings"}]}',
        # fisheries specialist
        f"Thought: correlate\nAction: correlate\nAction Input: {corr}",
        "Thought: done\nFinal Answer: Landings track previous-year chlorophyll [E1].",
        # synthesizer draft with a made-up number -> verifier fails
        "Weak upwelling lowers next-year landings.\n\n**Key findings**\n- Correlation r=0.12 [E1].",
        # revision after verifier feedback
        "Weak upwelling lowers next-year landings [E1].\n\n**Key findings**\n- Landings follow upwelling [E1].",
    ])
    orch = Orchestrator(settings, llm, store, kb)
    events = []
    result = await orch.run("Does upwelling affect oil sardine landings?", await _collect(events))
    assert result["plan"] == [{"specialist": "fisheries", "task": "correlate upwelling and landings"}]
    assert result["revisions"] == 1
    assert result["verification"]["passed"]
    assert result["uses_synthetic_data"]
    assert [r["id"] for r in result["references"]] == ["E1"]
    assert "**References**" in result["answer_markdown"]
    kinds = [k for k, _ in events]
    assert kinds[0] == "run_started" and kinds[-1] == "final"
    assert kinds.count("verification") == 2


async def test_orchestrator_falls_back_when_plan_is_prose(settings, store, kb):
    llm = ScriptedLLM([
        "Thought: hmm\nFinal Answer: I would look at coral data.",  # planner prose
        "not json either",  # structured repair fails
        "Thought: ok\nFinal Answer: No data needed.",  # specialist answers without a tool...
        "Thought: still ok\nFinal Answer: No data needed.",  # ...is nudged once, then accepted
        "Answer without numbers.",  # synthesizer
    ])
    orch = Orchestrator(settings, llm, store, kb)
    result = await orch.run("Tell me about coral bleaching on reefs", await _collect([]))
    assert result["plan_source"] == "keyword fallback"
    assert result["plan"][0]["specialist"] == "biodiversity"
