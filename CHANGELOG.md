# Changelog

All dates 2026. Milestone 1 entry is a placeholder for the team to fill in.

## 2026-11-22: Milestone 2

### Built
- Multi-agent pipeline: planner (P1), four specialist roles (P2), synthesizer (P3), revision (P4), evidence critic (P5), plan-repair fallback (P6). All run on `qwen3:8b` through a local Ollama server.
- ReAct engine with plain-text Thought / Action / Observation turns, stop sequences, JSON input parsing, a duplicate-call guard and a tool budget.
- Eleven tools: dataset listing/description, structured query, read-only SQL, species lookup, trend (OLS + Mann-Kendall + Sen's slope), lagged correlation, anomaly / monthly heatwave detection, traced calculator, knowledge-note search, live OBIS occurrences.
- Evidence ledger (E1, E2, ...) and a deterministic number verifier; one automatic revision when checks fail.
- FastAPI service with streaming, run history, health checks and optional API key; Docker and Compose files; CI workflow.
- Streamlit app (`code/streamlit_app.py`) with live mode and a saved-test-runs mode.
- Demo database (synthetic, labelled) and cited background notes.

### Prompt iterations (details in docs/prompt-iteration-log.md)
- Planner v1 → v2 → v3: fewer redundant steps, JSON-only final answer, data guide with valid filter values.
- Specialist v1 → v2 → v3: data guide with valid values, clearer filter docs (list vs range), nudge to use a tool before answering.
- Synthesizer v1 → v2 → v3: unnumbered lead paragraph, citations in the lead, no merging of listed items into ranges.
- Critic v1 → v2 → v3: accept simple threshold comparisons and standard proxies.

### Fixed after real runs
- Grouped queries returned an arbitrary row per group (mixed Gulf of Mannar sites into a Lakshadweep answer): unaggregated columns now rejected, group columns always returned.
- Monthly series were silently cut at 200 rows (lost 2020-2024): analysis queries use their own row limit.
- Anomaly tool year filters removed the baseline years: year filters now limit reporting only.
- Whitespace-only questions passed validation.

### Repository
- Restructured into `prompts/`, `code/`, `test-data/`, `docs/` for Milestone 2.
- Test table: 15 inputs (7 normal, 5 edge, 3 invalid) run on qwen3:8b; outputs saved in `test-data/outputs/`.

## `<date>`: Milestone 1
- `<what the team submitted for Milestone 1>`
