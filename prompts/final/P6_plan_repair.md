# P6 · Plan repair (fallback)

| Field | Value |
|---|---|
| Prompt ID and name | P6 · Plan repair (fallback) |
| Step in the workflow | Step 1 fallback: only when the planner's Final Answer is not valid JSON |
| Purpose | Convert a prose plan into the JSON plan format. |
| Tool and model | ORCA-Prompt (Python) · qwen3:8b via Ollama (local) |
| Settings | temperature 0.1, num_ctx 8192, think=false; structured output (Ollama format = JSON schema) |
| Number of examples included | 0 |
| Output format | JSON {"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "object", "properties": {"specialist": {"type": "string", "enum": ["oceanography", "fisheries", "biodiversity", "context"]}, "task": {"type": "string"}}, "required": ["specialist", "task"]}}}, "required": ["steps"]} |

## Full system prompt

```text
Convert the plan below into JSON with a 'steps' list. Each step has 'specialist' (one of ['oceanography', 'fisheries', 'biodiversity', 'context']) and 'task'.
```

## Full user prompt template

```text
Question: {question}

Plan text:
{planner_output}
```

## What each variable holds

- `{question}`: the user's question
- `{planner_output}`: the planner's non-JSON Final Answer

## Sample run (real input and actual output)

Recorded in `test-data/outputs/run-v3-qwen3-8b/T06.json` · model qwen3:8b · 3.77 s

**Input (messages after the system prompt):**

[user]
```text
Question: How many OBIS occurrence records exist for whale sharks in the NE Arabian Sea since 2000?

Plan text:
There are 12 OBIS occurrence records for whale sharks (Rhincodon typus) in the NE Arabian Sea since 2008 [D-OCC]. Note that this dataset is synthetic demo data.
```

**Actual output:**
```text
{
  "steps": [
    {
      "specialist": "oceanography",
      "task": "Identify the geographic region and time period of interest: NE Arabian Sea and since 2000."
    },
    {
      "specialist": "fisheries",
      "task": "Query the OBIS occurrence records database for whale sharks (Rhincodon typus) within the specified region and time period."
    },
    {
      "specialist": "biodiversity",
      "task": "Count the number of occurrence records retrieved from the OBIS database for whale sharks in the NE Arabian Sea since 2000."
    },
    {
      "specialist": "context",
      "task": "Note that the dataset is synthetic demo data and provide appropriate context for the results."
    }
  ]
}
```
