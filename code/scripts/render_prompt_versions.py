"""Render the full text of earlier prompt versions (v1, v2) and the final one (v3) into prompts/v*/.

Earlier versions are produced from today's code by putting back the exact wording
that was replaced, so each file shows the complete prompt as the model saw it.
See docs/prompt-iteration-log.md for what changed and what was observed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orca.agents import roles, synthesizer  # noqa: E402
from orca.agents.react import ReActAgent  # noqa: E402
from orca.agents.roles import PLANNER_TOOLS, SPECIALISTS, data_guide, planner_prompt, specialist_prompt  # noqa: E402
from orca.config import Settings  # noqa: E402
from orca.data.store import MarineStore  # noqa: E402
from orca.tools import ALL_TOOLS  # noqa: E402
from orca.tools import analysis as an  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
S = Settings(_env_file=None)
STORE = MarineStore(S.data_db)

# ---- wording that later versions replaced -------------------------------------------------
PLANNER_V1_RULES = (
    "- Use as few steps as the question needs. A single-fact question needs one step.",
    "- You may call a tool to check what the data contains, but usually the data list above is enough.\n\n"
    "Your Final Answer must be only a JSON object, no prose:",
)
PLANNER_V2_RULES = (
    "- Use as few steps as the question needs. Most questions need 1 or 2 steps; never add a step that only repeats another.",
    "- The data list above is usually enough. Call a tool only if you truly cannot tell which data applies.\n\n"
    'Reply with a Thought, then "Final Answer:" followed by only a JSON object, no prose:',
)
QUERY_DATA_OLD = ("'where' takes exact values, lists, or operators (>=, <=, >, <, !=, like, between); "
                  "'group_by' and 'order_by' take column names ")
QUERY_DATA_NEW = ("'where' takes exact values, lists (= any of these exact values), or operators (>=, <=, >, <, !=, "
                  "like, between); for a year range use {\"year\": {\"between\": [2014, 2024]}}; "
                  "'group_by' and 'order_by' take column names ")
SERIES_OLD = 'a series spec {"dataset": id, "metric": column, "agg": "avg|sum|min|max", "where": {...}} aggregated to one value per year'
ANOMALY_NEW_TAIL = (" and is always taken from the full series; a year filter in 'where' only limits which years are reported.")

SYNTH_V1_LAYOUT = """Write in Markdown:
1. A direct answer in 1-3 sentences.
2. "**Key findings**" as 2-6 bullets. End every bullet with its evidence ids in square brackets, e.g. [E2] or [E2, E4].
3. "**Caveats**" as 1-3 short bullets (data limits, short records, correlation vs causation, synthetic demo data when any cited evidence is synthetic)."""
SYNTH_V2_LEAD = "- First, a direct answer in 1-3 sentences (a plain paragraph, no heading or numbering)."
SYNTH_V3_LEAD = "- First, a direct answer in 1-3 sentences (a plain paragraph, no heading or numbering), with [E#] citations after any number."
SYNTH_V3_LIST_RULE = ("- When the evidence lists items (months, years, sites, species), report them exactly. Do not merge them into "
                      "ranges such as \"January to December 2017\" unless every item in that range appears in the evidence; "
                      "summarise with counts per year instead if the list is long.\n")
CRITIC_V2_LINE = ("A simple comparison of an evidence number with a threshold stated in the evidence (e.g. 12.1 is above 12) "
                  "counts as supported, as do rounding and paraphrase.\n")
CRITIC_V3_LINE = ("Standard proxies count as supported when the question or findings use them (e.g. monsoon chlorophyll-a as "
                  "an indicator of upwelling strength); do not report a claim only because the evidence uses the proxy's name.\n")


def old_data_guide(store: MarineStore) -> str:
    """v1 data guide: table and column names, plus regions/states/species only."""
    lines = []
    for d in store.datasets:
        tag = " [SYNTHETIC demo data]" if d.synthetic else ""
        lines.append(f"- {d.dataset_id} = table {d.table_name}: {d.title}{tag}\n  columns: {', '.join(d.columns)}")
    lines.append(f"Regions: {store.distinct_values('ocean_monthly', 'region')}")
    lines.append(f"States (fish_landings): {store.distinct_values('fish_landings', 'state')}")
    lines.append(f"Landed species: {store.distinct_values('fish_landings', 'scientific_name')}")
    return "\n".join(lines)


def with_old_tool_docs(text: str) -> str:
    text = text.replace(QUERY_DATA_NEW, QUERY_DATA_OLD)
    text = text.replace(an.SERIES_DOC, SERIES_OLD)
    return text.replace(ANOMALY_NEW_TAIL, ".")


def planner(version: int) -> str:
    # v2 changed the rules (after run 1); v3 changed the data guide (after run 2).
    guide = old_data_guide(STORE) if version <= 2 else data_guide(STORE)
    agent = ReActAgent("planner", planner_prompt(guide, S.max_plan_steps), [ALL_TOOLS[t] for t in PLANNER_TOOLS], None, "m",
                       max_iterations=2)
    text = agent.system_prompt()
    if version == 1:
        text = text.replace(PLANNER_V2_RULES[0], PLANNER_V1_RULES[0]).replace(PLANNER_V2_RULES[1], PLANNER_V1_RULES[1])
    return text


def specialist(version: int) -> str:
    spec = SPECIALISTS["oceanography"]
    guide = old_data_guide(STORE) if version == 1 else data_guide(STORE)
    agent = ReActAgent(spec.key, specialist_prompt(spec, guide), [ALL_TOOLS[t] for t in spec.tools], None, "m",
                       max_iterations=S.max_react_iterations)
    text = agent.system_prompt()
    if version < 3:
        text = with_old_tool_docs(text)
        text += "\n\n(v1/v2: no follow-up nudge when the agent answers without using a tool)"
    else:
        text += ("\n\n(v3 adds this system turn when a specialist answers before using any tool:)\n"
                 "Observation: You have not used any tool for your step yet. Earlier findings are context, not evidence "
                 "for your step. Call a tool now to get evidence for your step. Only if an earlier evidence id already "
                 "contains exactly what your step asks for may you answer directly, citing that id.")
    return text


def synth(version: int) -> str:
    text = synthesizer.SYSTEM
    if version == 1:
        start = text.index("Write in Markdown with this layout:")
        end = text.index("Strict rules:")
        text = text[:start] + SYNTH_V1_LAYOUT + "\n\n" + text[end:]
    elif version == 2:
        text = text.replace(SYNTH_V3_LEAD, SYNTH_V2_LEAD).replace(SYNTH_V3_LIST_RULE, "")
    return text


def critic(version: int) -> str:
    text = synthesizer.CRITIC_SYSTEM
    if version <= 2:
        text = text.replace(CRITIC_V3_LINE, "")
    if version == 1:
        text = text.replace(CRITIC_V2_LINE, "")
    return text


def main() -> None:
    assert roles  # imported for completeness
    for v in (1, 2, 3):
        out = ROOT / "prompts" / f"v{v}"
        out.mkdir(parents=True, exist_ok=True)
        for name, fn in (("P1_planner", planner), ("P2_specialist_oceanography", specialist),
                         ("P3_synthesizer", synth), ("P5_critic", critic)):
            (out / f"{name}.txt").write_text(fn(v) + "\n", encoding="utf-8")
        print("wrote", out)


if __name__ == "__main__":
    main()
