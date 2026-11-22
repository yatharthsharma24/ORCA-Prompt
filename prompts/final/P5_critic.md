# P5 · Evidence critic

| Field | Value |
|---|---|
| Prompt ID and name | P5 · Evidence critic |
| Step in the workflow | Step 4 of 4: check claims against evidence |
| Purpose | Find claims a number check cannot catch: wrong site/year attached to a number, unsupported thresholds. |
| Tool and model | ORCA-Prompt (Python) · qwen3:8b via Ollama (local) |
| Settings | temperature 0.1, num_ctx 8192, think=false; structured output (Ollama format = JSON schema) |
| Number of examples included | 0 worked examples; 1 inline comparison example (12.1 is above 12). |
| Output format | JSON {"type": "object", "properties": {"problems": {"type": "array", "items": {"type": "object", "properties": {"claim": {"type": "string"}, "problem": {"type": "string"}}, "required": ["claim", "problem"]}}}, "required": ["problems"]} |

## Full system prompt

```text
You are the ORCA Evidence Critic. You check a draft answer against the evidence records it cites.

Report a claim only if:
- the evidence contradicts it, or
- it attaches a number to the wrong site, region, year, species or variable, or
- it states a threshold, category or fact that the cited evidence does not contain.

A simple comparison of an evidence number with a threshold stated in the evidence (e.g. 12.1 is above 12) counts as supported, as do rounding and paraphrase.
Standard proxies count as supported when the question or findings use them (e.g. monsoon chlorophyll-a as an indicator of upwelling strength); do not report a claim only because the evidence uses the proxy's name.
Do not report style, tone or missing detail. Be strict but do not invent problems. If every claim is supported, return an empty list.
```

## Full user prompt template

```text
Question: {question}

Evidence:
{evidence}

Draft answer:
{answer}

List the unsupported claims as JSON.
```

## What each variable holds

- `{question}`: the user's question
- `{evidence}`: evidence records as in P3 (up to 1500 characters each)
- `{answer}`: the synthesizer draft

## Sample run (real input and actual output)

Recorded in `test-data/outputs/run-v3-qwen3-8b/T03.json` · model qwen3:8b · 7.42 s

**Input (messages after the system prompt):**

[user]
```text
Question: How severe was coral heat stress at Lakshadweep reefs in 2016 and 2024, and what do the DHW values mean for bleaching?

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

[K-DHW#4] Degree Heating Weeks and coral bleaching alert levels: The max_dhw column in the ORCA demo table coral_reef_monitoring is a monthly approximation of the CRW method (monthly HotSpots of at least 1 degC summed over three months and converted to weeks). It reproduces the idea, not the CRW product.

Draft answer:
Coral heat stress at Lakshadweep reefs was severe in both 2016 and 2024, with max_dhw values exceeding Alert Level 3 thresholds, indicating extreme heat stress and significant bleaching [E1][E2].

- In 2016, the max_dhw was 11.4, which falls within Alert Level 3 (12 degC-weeks), indicating extreme heat stress and likely severe bleaching [E1][E2].
- In 2024, the max_dhw increased to 12.1, further confirming extreme heat stress and significant coral mortality [E1][E2].

Caveats: The evidence is based on synthetic data [E1]. The DHW thresholds for bleaching levels are derived from NOAA Coral Reef Watch guidelines [E2].

List the unsupported claims as JSON.
```

**Actual output:**
```text
{
  "problems": [
    {
      "claim": "Coral heat stress at Lakshadweep reefs was severe in both 2016 and 2024, with max_dhw values exceeding Alert Level 3 thresholds, indicating extreme heat stress and significant bleaching",
      "problem": "The evidence does not state that max_dhw values exceeded Alert Level 3 thresholds. The 2016 max_dhw is 11.4, which is below the Alert Level 3 threshold of 12. The 2024 max_dhw is 12.1, which is above the threshold, but the claim generalizes both years as exceeding the threshold."
    },
    {
      "claim": "In 2016, the max_dhw was 11.4, which falls within Alert Level 3 (12 degC-weeks), indicating extreme heat stress and likely severe bleaching",
      "problem": "The evidence does not state that 11.4 falls within Alert Level 3. The threshold for Alert Level 3 is 12, and 11.4 is below that threshold. The claim incorrectly categorizes 11.4 as falling within Alert Level 3."
    },
    {
      "claim": "In 2024, the max_dhw increased to 12.1, further confirming extreme heat stress and significant coral mortality",
      "problem": "The evidence does not state that 12.1 confirms extreme heat stress or significant coral mortality. The evidence only provides the DHW value and the percentage of bleached corals. The claim adds a threshold-based interpretation that is not explicitly supported by the evidence."
    }
  ]
}
```
