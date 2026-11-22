# ORCA-Prompt: A Multi-Agent ReAct Assistant for Marine Ecosystem Reasoning

**SCSE4041 Mini Project · Problem statement SIH26176**

ORCA-Prompt answers questions about the ocean around India: sea temperature, chlorophyll, fish landings, coral bleaching and protected species. A **planner** agent splits the question into steps. **Specialist** agents (oceanography, fisheries, biodiversity, context) work each step with ReAct prompts (Thought → Action → Observation) and real data tools. A **synthesizer** writes the answer with numbered evidence citations `[E1]`. A **verifier** then checks every number in the answer against the cited evidence, and a **critic** agent checks that each claim is supported. Every LLM call runs on a local model, `qwen3:8b`, through Ollama.

| | |
|---|---|
| Prototype (Streamlit) | `<add Streamlit Community Cloud URL after deploying>` |
| Repository | https://github.com/yatharthsharma24/ORCA-Prompt |
| Reel | `<add Drive / unlisted YouTube URL>` |

## Team

| Member | Enrollment no. | Main task |
|---|---|---|
| `<name>` | `<S24CSEU....>` | `<task>` |
| `<name>` | `<S24CSEU....>` | `<task>` |
| `<name>` | `<S24CSEU....>` | `<task>` |

## How to open it

**Online:** open the prototype link. In **Saved test runs** mode you can browse the real recorded test runs (answer, agent trace, evidence and verification) without any setup. **Ask live** works whenever the team's model server is online; the sidebar shows its status.

**On your own machine** (Windows, macOS or Linux; Python 3.11+ and [Ollama](https://ollama.com)):

```bash
ollama pull qwen3:8b
python -m venv .venv
.venv/Scripts/activate            # macOS/Linux: source .venv/bin/activate
pip install -r code/requirements.txt
cd code
python -m orca.data.generate      # builds the demo database
streamlit run streamlit_app.py    # Streamlit app
# or: uvicorn orca.api.app:create_app --factory --port 8000   (FastAPI web UI)
```

## Repository layout

```
README.md          this file
CHANGELOG.md       dated list of changes
prompts/
  v1/ v2/ v3/      earlier prompt versions (full text), v3 = current
  final/           one prompt sheet per final prompt (P1-P6), with a real sample run
code/              Python package `orca`, Streamlit app, tests, Docker files
  streamlit_app.py
  orca/            agents, tools, verifier, API, web UI
  tests/           48 offline unit/integration tests
  scripts/         run_tests.py (test table), export_prompts.py, render_prompt_versions.py
test-data/
  inputs.json      the 15 test-table inputs with expected outputs and checks
  outputs/         saved outputs of every run (full answers + every LLM call)
docs/
  diagrams/        prompt workflow, task decomposition, I/O flow, architecture, agent loop
  prompt-iteration-log.md
  test-table.md
```

## Data honesty

Four of the five local tables (ocean conditions, landings, survey/eDNA records, coral monitoring) are **synthetic demo data**. They are generated with documented regional patterns so the agents have something to reason about, but they are not real observations, and every answer that uses them says so. The species reference table (checked against OBIS/WoRMS), the live OBIS occurrence records and the cited background notes are real. `code/orca/data/ingest.py` loads real CSV data with its own citation.

More detail on the code, deployment and API is in [`code/README.md`](code/README.md).
