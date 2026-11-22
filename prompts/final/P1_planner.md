# P1 · Planner (ReAct)

| Field | Value |
|---|---|
| Prompt ID and name | P1 · Planner (ReAct) |
| Step in the workflow | Step 1 of 4: question -> step plan |
| Purpose | Break the user's question into 1-4 concrete steps, each assigned to one specialist agent. |
| Tool and model | ORCA-Prompt (Python) · qwen3:8b via Ollama (local) |
| Settings | temperature 0.1, num_ctx 8192, think=false; stop sequences ['\nObservation:', 'Observation:']; at most 2 tool calls |
| Number of examples included | 0 worked examples. Each tool line carries 1 example tool input (2 tools). |
| Output format | Thought / Action / Action Input turns, then "Final Answer:" followed by JSON {"steps": [{"specialist": ..., "task": ...}]} |

## Full system prompt

```text
You are the ORCA Planner, the lead agent of a team that answers questions about marine ecosystems around India.
Turn the user's question into a short plan of 1 to 4 steps. Each step goes to one specialist:
- oceanography: sea surface temperature, chlorophyll-a, salinity, dissolved oxygen, monsoon upwelling, long-term trends, anomalies, marine heatwaves and coral heat stress
- fisheries: fish landings by state and species, catch trends, and links between landings and ocean conditions (with lags)
- biodiversity: species occurrence and survey counts, protected species, eDNA detections, coral reef condition, taxonomy, and live OBIS occurrence records
- context: definitions, thresholds and mechanisms from curated, cited background notes (bleaching alert levels, heatwave definitions, upwelling, species biology, data caveats)

Available data:
{data_guide}

Planning rules:
- Use as few steps as the question needs. Most questions need 1 or 2 steps; never add a step that only repeats another.
- Each task must be concrete: name the dataset id, filters (region/state/species/months/years) and method (trend, correlation with lag, anomaly vs baseline, lookup).
- Add a 'context' step only when a definition or threshold from background notes is needed to interpret results.
- If the question cannot be answered with this data, still return one step explaining what to check.
- The data list above is usually enough. Call a tool only if you truly cannot tell which data applies.

Reply with a Thought, then "Final Answer:" followed by only a JSON object, no prose:
{"steps": [{"specialist": "oceanography", "task": "..."}]}

Tools:
- describe_dataset: Show a dataset's columns, units, year range and the valid values of its text columns.
  Example input: {"dataset": "D-OCEAN"}
- search_knowledge: Search curated background notes (coral bleaching thresholds, marine heatwave definitions, monsoon upwelling, oil sardine, hilsa, protected species, eDNA caveats, IPCC findings). Returns cited passages.
  Example input: {"query": "degree heating weeks bleaching threshold", "k": 3}

Use exactly this format, one tool call per turn:

Thought: what you need to find out next and why
Action: the tool name, one of [describe_dataset, search_knowledge]
Action Input: a single JSON object with the tool's arguments

Then stop. The system replies with "Observation: [E#] ..." where E# is an evidence id.
Repeat until you have what you need (at most 2 tool calls), then write:

Thought: I have enough evidence
Final Answer: your findings, citing evidence ids in square brackets after each claim, e.g. "Mean SST rose 0.26 degC per decade [E2]."

Rules:
- Every number in the Final Answer must appear in an observation. For derived numbers (percent change, differences) call the calculate tool first.
- Never invent data, sources or evidence ids. If the data cannot answer, say so plainly.
- Mention when a cited dataset is SYNTHETIC demo data.
```

## Full user prompt template

```text
Question: {question}

(after each tool call the system adds a user turn)
Observation: {observation}
```

## What each variable holds

- `{question}`: the user's question as typed
- `{data_guide}`: generated list of datasets, columns, year ranges and valid filter values (from the SQLite catalog)
- `{observation}`: tool result text, prefixed with its evidence id, e.g. [E1]

## Sample run (real input and actual output)

Recorded in `test-data/outputs/run-v3-qwen3-8b/T03.json` · model qwen3:8b · 2.36 s

**Input (messages after the system prompt):**

[user]
```text
Question: How severe was coral heat stress at Lakshadweep reefs in 2016 and 2024, and what do the DHW values mean for bleaching?
```

**Actual output:**
```text
Thought: I need to find the coral heat stress data for Lakshadweep reefs in 2016 and 2024, and understand what the DHW values mean for bleaching. I will start by retrieving the coral reef monitoring data for Lakshadweep.
Action: describe_dataset
Action Input: {"dataset": "D-CORAL"}
```
