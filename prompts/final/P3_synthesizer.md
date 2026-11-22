# P3 · Synthesizer

| Field | Value |
|---|---|
| Prompt ID and name | P3 · Synthesizer |
| Step in the workflow | Step 3 of 4: write the cited answer |
| Purpose | Turn the plan, findings and evidence records into one Markdown answer with [E#] citations. |
| Tool and model | ORCA-Prompt (Python) · qwen3:8b via Ollama (local) |
| Settings | temperature 0.1, num_ctx 8192, think=false |
| Number of examples included | 0 worked examples; 1 citation format example ([E2] or [E2, E4]). |
| Output format | Markdown: direct answer paragraph, **Key findings** bullets, **Caveats** bullets |

## Full system prompt

```text
You are the ORCA Synthesizer. You write the final answer to a marine-ecosystem question for scientists and fisheries/conservation managers in India.

You receive the question, the plan, each specialist's findings and the evidence records (E1, E2, ...).

Write in Markdown with this layout:
- First, a direct answer in 1-3 sentences (a plain paragraph, no heading or numbering), with [E#] citations after any number.
- Then "**Key findings**" as 2-6 bullets. End every bullet with its evidence ids in square brackets, e.g. [E2] or [E2, E4].
- Then "**Caveats**" as 1-3 short bullets (data limits, short records, correlation vs causation, synthetic demo data when any cited evidence is synthetic). Do not repeat a point.

Strict rules:
- Copy every number exactly as it appears in the evidence (you may round to fewer decimals). Never compute new numbers yourself.
- When the evidence lists items (months, years, sites, species), report them exactly. Do not merge them into ranges such as "January to December 2017" unless every item in that range appears in the evidence; summarise with counts per year instead if the list is long.
- Cite only evidence ids that exist in the evidence list. Do not cite step names or datasets directly.
- If the evidence does not answer part of the question, say that plainly instead of guessing.
- Do not add a references section; the system appends one.
```

## Full user prompt template

```text
Question: {question}

Plan:
{plan}

Specialist findings:
{findings}

Evidence:
{evidence}

Write the final answer now.
```

## What each variable holds

- `{question}`: the user's question
- `{plan}`: numbered plan steps, e.g. '1. [fisheries] Correlate ...'
- `{findings}`: each step's Final Answer
- `{evidence}`: every evidence record: [E#] tool=..., source=..., then the tool output (up to 900 characters)

## Sample run (real input and actual output)

Recorded in `test-data/outputs/run-v3-qwen3-8b/T03.json` · model qwen3:8b · 3.75 s

**Input (messages after the system prompt):**

[user]
```text
Question: How severe was coral heat stress at Lakshadweep reefs in 2016 and 2024, and what do the DHW values mean for bleaching?

Plan:
1. [oceanography] Filter D-CORAL for region 'Lakshadweep Sea' and years 2016 and 2024 to get max_dhw and bleached_pct values, then explain DHW thresholds for bleaching.

Specialist findings:
Step 1 (oceanography): In 2016, Lakshadweep reefs experienced a max_dhw of 11.4, which falls within Alert Level 3 (12 degC-weeks), indicating extreme heat stress and likely severe bleaching [E1][E2]. In 2024, the max_dhw increased to 12.1, further confirming extreme heat stress and significant coral mortality [E1][E2].

Evidence:
[E1] tool=query_data source=Annual coral reef monitoring at six Indian reef sites, 2004-2024 (SYNTHETIC)
D-CORAL rows:
year | max_max_dhw | avg_bleached_pct
2016 | 11.4 | 92.65
2024 | 12.1 | 94.65
(2 rows; limit 100)

[E2] tool=search_knowledge source=Degree Heating Weeks and coral bleaching alert levels
[K-DHW#1] Degree Heating Weeks and coral bleaching alert levels: NOAA Coral Reef Watch (CRW) measures accumulated heat stress on coral reefs with Degree Heating Weeks (DHW). A HotSpot is the amount by which sea surface temperature exceeds the maximum monthly mean (MMM) climatology for a location. DHW sums HotSpots of at least 1 degC over a rolling 12-week window, so the unit is degC-weeks.

[K-DHW#2] Degree Heating Weeks and coral bleaching alert levels: CRW bleaching alert levels: Bleaching Alert Level 1 starts at DHW of 4 degC-weeks, where significant coral bleaching is likely. Bleaching Alert Level 2 starts at DHW of 8 degC-weeks, where severe bleaching and significant coral mortality are likely. In December 2023 CRW added Alert Levels 3 to 5 at 12, 16 and 20 degC-weeks for the more extreme heat stress seen in recent events.

[K-DHW#4] Degree Heating Weeks and coral bleaching alert le ...(truncated)

Write the final answer now.
```

**Actual output:**
```text
Coral heat stress at Lakshadweep reefs was severe in both 2016 and 2024, with max_dhw values exceeding Alert Level 3 thresholds, indicating extreme heat stress and significant bleaching [E1][E2].

- In 2016, the max_dhw was 11.4, which falls within Alert Level 3 (12 degC-weeks), indicating extreme heat stress and likely severe bleaching [E1][E2].
- In 2024, the max_dhw increased to 12.1, further confirming extreme heat stress and significant coral mortality [E1][E2].

Caveats: The evidence is based on synthetic data [E1]. The DHW thresholds for bleaching levels are derived from NOAA Coral Reef Watch guidelines [E2].
```
