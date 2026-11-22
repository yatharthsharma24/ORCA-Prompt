# ORCA-Prompt diagrams

All diagrams describe the code in `code/orca/` as built. Prompt IDs (P1–P6) match `prompts/final/`.

## 1. Prompt workflow

```mermaid
flowchart LR
    Q([User question]) --> V{Input valid?<br/>3-1000 chars}
    V -- no --> R422([HTTP 422, no LLM call])
    V -- yes --> P1[P1 Planner<br/>ReAct, max 2 tool calls]
    P1 -->|JSON plan| S
    P1 -.->|not JSON| P6[P6 Plan repair<br/>JSON schema] -.-> S
    S[P2 Specialist per step<br/>ReAct, max 6 tool calls] -->|findings + E# ids| P3[P3 Synthesizer]
    P3 --> VER[Deterministic verifier<br/>numbers vs evidence]
    P3 --> P5[P5 Critic<br/>JSON schema]
    VER --> D{Problems?}
    P5 --> D
    D -- yes, once --> P4[P4 Revision] --> VER
    D -- no --> A([Answer + verification + references])
```

## 2. Task decomposition (example: T01)

```mermaid
flowchart TD
    Q["Did weak monsoon upwelling off Kerala affect<br/>oil sardine landings in the following year?"]
    Q --> S1["S1 Oceanography: Jun-Sep chlorophyll in SE Arabian Sea<br/>per year (upwelling proxy)"]
    Q --> S2["S2 Fisheries: correlate previous-year chlorophyll<br/>with Kerala oil sardine landings (lag 1)"]
    S1 --> T1["tool: anomaly / query_data → E1"]
    S2 --> T2["tool: correlate (lag_years=1) → E2"]
    T1 & T2 --> SYN["Synthesizer: answer citing E1, E2"]
    SYN --> CHK["Verifier + critic"]
```

## 3. Input–output flow

```mermaid
flowchart LR
    IN["Input<br/>question text"] --> PLAN["Plan JSON<br/>{steps:[{specialist, task}]}"]
    PLAN --> OBS["Tool observations<br/>[E1] rows / stats / OBIS / notes"]
    OBS --> LEDGER[("Evidence ledger<br/>E1..En: query, numbers, source citation")]
    LEDGER --> ANS["Draft answer<br/>Markdown with [E#]"]
    ANS --> REP["Verification report<br/>claim status, score, critic notes"]
    ANS & REP & LEDGER --> OUT["Output<br/>answer + references + trace (JSON / UI)"]
```

## 4. System architecture

```mermaid
flowchart TB
    subgraph Client
        ST["Streamlit app<br/>code/streamlit_app.py"]
        WEB["Web UI (FastAPI static)<br/>code/orca/web"]
    end
    subgraph App["ORCA-Prompt (Python)"]
        API["FastAPI + SSE<br/>orca/api/app.py"]
        ORC["Orchestrator<br/>orca/orchestrator.py"]
        AG["Agents: planner, 4 specialists,<br/>synthesizer, critic"]
        TOOLS["Tools: query_data, run_sql, trend, correlate,<br/>anomaly, calculate, species_lookup,<br/>search_knowledge, obis_occurrences"]
        VERI["Verifier (no LLM)"]
    end
    subgraph Data
        DB[("SQLite marine DB<br/>read-only, catalog with citations")]
        KB[("Knowledge notes<br/>NOAA CRW, IPCC, Hobday 2016 ...")]
        RUNS[("Run history")]
    end
    OLL["Ollama server<br/>qwen3:8b (local GPU)"]
    OBIS["OBIS API<br/>api.obis.org (real records)"]
    ST --> ORC
    WEB --> API --> ORC
    ORC --> AG --> OLL
    AG --> TOOLS
    TOOLS --> DB & KB & OBIS
    ORC --> VERI
    API --> RUNS
```

## 5. Agent loop (ReAct, used by P1 and P2)

```mermaid
flowchart TD
    START([System prompt: role + tools + format<br/>User: task]) --> LLM[LLM call<br/>stop at 'Observation:']
    LLM --> PARSE{Parse reply}
    PARSE -- "Final Answer" --> NUDGE{Specialist with<br/>0 tool calls?}
    NUDGE -- "yes, first time" --> MSG1["Observation: use a tool first"] --> LLM
    NUDGE -- no --> DONE([Findings with E# citations])
    PARSE -- "Action + JSON input" --> SAME{Same call<br/>made before?}
    SAME -- yes --> REUSE["Reuse earlier evidence id"] --> OBSV
    SAME -- no --> RUN[Run tool] --> OK{Error?}
    OK -- yes --> ERR["Observation: Tool error + hint"] --> LLM
    OK -- no --> LED["Add to ledger as E#"] --> OBSV["Observation: [E#] result"]
    OBSV --> BUDGET{Tool budget used up?}
    BUDGET -- no --> LLM
    BUDGET -- yes --> FORCE["Ask for Final Answer now"] --> DONE
    PARSE -- "neither (format error)" --> FMT["Observation: format error"] --> LLM
```
