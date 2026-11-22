# Prompt iteration log

Four prompts went through three versions each (12 saved versions). Each change was made on purpose after a
real run went wrong. The runs are saved in `test-data/outputs/iteration-evidence/` (runs 1-4) and
`test-data/outputs/run-v3-qwen3-8b/` (final test table, v3 prompts). Full text of every version is in
`prompts/v1/`, `prompts/v2/`, `prompts/v3/`; v3 is the final version (`prompts/final/`).

Model for every run: qwen3:8b via Ollama, temperature 0.1, num_ctx 8192, thinking off.

Some fixes were code changes in tools rather than prompt changes. Those are listed separately at the end so
they are not counted as prompt versions.

## P1 · Planner

| Version | What changed | Why it was changed | Result after the change |
|---|---|---|---|
| v1 | First planner prompt: role, specialist list, data list (table + column names), rules, "Final Answer must be only a JSON object" | Starting point | Run 1 (upwelling question): planner used both tool calls on `describe_dataset`, its Final Answer was prose, so the plan had to come from the JSON repair prompt. Plan had **4 steps**; steps 3 and 4 used **0 tools** and repeated step 2's findings. Run took 69.9 s for 2 evidence records. |
| v2 | Rules: "Most questions need 1 or 2 steps; never add a step that only repeats another"; "Call a tool only if you truly cannot tell which data applies"; Final Answer must follow a Thought and be JSON only. The forced-finish message also asks for the JSON plan. | Too many steps and non-JSON output in v1 | Run 2 (coral): JSON plan parsed directly, **2 steps**. Run 3 and the Andaman run: **1 step** each. Run 2 still filtered `site = 'Lakshadweep Sea'`, which is not a valid site value. |
| v3 | Context: the data list now shows the year range of each table and the valid values of every short text column (site, reef_area, region, state, species) | The planner wrote filters that didn't exist (`site = 'Lakshadweep Sea'`) | Run 3 used `region = 'Lakshadweep Sea'`, a valid value. Final test table (13 questions reached the planner): **9** plans parsed directly, **4** needed the JSON repair prompt (T06, T08, T11, T15). Over-planning remains in 3 of 13 (T06 and T08 used 4 steps, T15 used 3). T09 mapped "oil sardine" to *Rastrelliger kanagurta* (mackerel), because the data list shows scientific names only. |

## P2 · Specialist (ReAct)

| Version | What changed | Why it was changed | Result after the change |
|---|---|---|---|
| v1 | Role prompt + tools + ReAct format; data list with column names only | Starting point | Run 2 (coral heat stress): first query used an invalid site value and returned 0 rows; the second query grouped all 6 reef sites by site but did not return the site column. The answer gave **13.7 °C-weeks** (Kurusadai, Gulf of Mannar) as the Lakshadweep 2016 value. The number check passed it (2/2) because 13.7 is in the evidence. |
| v2 | Context: same richer data list as P1 v3 (valid site / reef_area / region values, year ranges) | Agent guessed filter values | Run 3: first query used `region = 'Lakshadweep Sea'` and returned 2 rows (2016: 11.4, 2024: 12.1), 38.8 s. Run 4: olive-ridley step 2 answered with **0 tool calls**, copying step 1, and its IUCN claim ended up as `[unverified]`. Heatwave question: **6 failed** `anomaly` calls with `year: [2014, 2024]` (meant as a range), and the answer wrongly said the baseline was insufficient. |
| v3 | Tool descriptions: lists mean exact values, use `{"between": [a, b]}` for year ranges; anomaly baseline is always taken from the full series; series metric is a plain column name. New system turn: a specialist that answers before using any tool is told once to use a tool first. | Copying earlier steps; misread year filters | Olive ridley re-run: step 2 called `species_lookup` and cited **Vulnerable [E3]**, 1/1 numeric claims verified, 0 critic notes, 32.5 s. Heatwave re-run: **1** anomaly call with `between`, 77 flagged months listed, 40.8 s. |

## P3 · Synthesizer

| Version | What changed | Why it was changed | Result after the change |
|---|---|---|---|
| v1 | Numbered layout: "1. A direct answer … 2. Key findings … 3. Caveats" | Starting point | Run 1: the answer itself began with "1."; the synthetic-data caveat appeared twice. |
| v2 | Lead is "a plain paragraph, no heading or numbering"; "Do not repeat a point" | Numbering leaked into the answer; repeated caveats | Runs 2-3: no numbering in the lead. Run 3 revision: the 2 lead sentences carried numbers but no citation, so the verifier marked **2 claims uncited** and the score fell to **0.5**. |
| v3 | Lead must cite [E#] after any number; listed items (months, sites, years) must be reported exactly, never merged into ranges unless every item is in the evidence | Uncited lead; run 4 heatwave answer said "January to December 2017" when the evidence had only 5 flagged months in 2017 | Andaman run: **5/5** numeric claims verified (score 1.0); T02 in the test table: 4/4. Heatwave re-run: listed the flagged months exactly. But in the test table (T04) the bullets were exact while the lead paragraph again merged months into ranges that include unflagged months, so this rule is only partly followed (T04 marked Fail). |

## P5 · Evidence critic

| Version | What changed | Why it was changed | Result after the change |
|---|---|---|---|
| v1 | Report a claim only if the evidence contradicts it, a number is attached to the wrong site/year/species, or a threshold is not in the evidence | Starting point (added after run 2's wrong answer passed the number check) | Run 3 draft: **3 notes, all correct** (11.4 called "Alert Level 3"; Level 2 given as "8-16"). The revision fixed them, but the critic then flagged **2 valid comparisons** ("11.4 is below 12") as unsupported inference. |
| v2 | Simple comparisons with a threshold stated in the evidence count as supported, as do rounding and paraphrase | False positives on comparisons | Andaman run: **0** critic notes. Run 4 upwelling question: **5 notes**, all saying the evidence "does not mention upwelling" although chlorophyll was the stated upwelling proxy. They triggered an unneeded revision (run took 75.3 s). |
| v3 | Standard proxies named in the question or findings count as supported | Proxy false positives | Final test table: 0 critic notes on T02-T09, T11 and T15. On T01 (same upwelling question) the specialist used the wrong region; the critic raised **4** notes and a revision ran, but the final answer was still wrong (T01 marked Fail). The proxy false positives from run 4 did not reappear. |

## Code fixes found by the same runs (not counted as prompt versions)

- `query_data`: grouped queries now always return the group columns and reject unaggregated columns (cause of the 13.7 error).
- `anomaly`: year filters limit which years are reported, not the baseline (cause of the 6 failed calls).
- Analysis tools: monthly series were cut at the 200-row agent limit, losing 2020-2024; they now use a separate limit.
- Verifier: did not read scientific notation (`p=7.56e-10`), so run 1 failed verification wrongly.
- API: whitespace-only questions passed input validation.
