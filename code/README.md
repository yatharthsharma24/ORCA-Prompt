# ORCA-Prompt

**A multi-agent ReAct assistant for marine ecosystem reasoning** (SIH26176)

You ask a question about ocean conditions, fisheries or biodiversity around India. A planner agent breaks it into steps. Specialist agents work each step with ReAct (Thought → Action → Observation) against real tools and data. Then a synthesizer writes the answer, and every number in it is checked against the evidence it cites before you see it.

Every LLM call runs on a local model through [Ollama](https://ollama.com). No cloud API keys are needed. The default model is `qwen3:8b`.

```
question
   │
   ▼
Planner (ReAct: describe_dataset, search_knowledge) ──► JSON step plan
   │
   ├─► Oceanography Analyst  ┐
   ├─► Fisheries Analyst     ├─ ReAct loops over tools; every data-bearing
   ├─► Biodiversity Analyst  │  tool call becomes numbered evidence E1, E2, ...
   └─► Context Specialist    ┘
   │
   ▼
Synthesizer ──► draft answer with [E#] citations
   │
   ▼
Verifier (deterministic number check) + Critic agent (claim support)
   │   └─ problems? ──► one revision round
   ▼
answer + verification report + references (dataset / OBIS / literature citations)
```

## What "data-verified" means here

- **Evidence ledger.** Every tool result that carries data is stored as `E1`, `E2`, …, along with the exact query, the numbers it returned and its source citation. Agents only see observations tagged with these ids.
- **Number verifier (no LLM).** The answer is split into claims. Every number in a claim must match a number in the evidence it cites, allowing for rounding (`49,000` matches `48,670`), percent scaling (`88%` matches `r2=0.8838`) and scientific notation. Each claim comes out as `verified`, `miscited`, `uncited`, `unsupported` or `invalid_citation`. Any `unsupported` claim or bad citation fails the check.
- **Critic agent.** A second LLM pass looks for claims the number check can't catch, such as the right number attached to the wrong site, or a threshold the cited note doesn't contain.
- **Traced arithmetic.** Derived numbers (percent change, differences) must come from the `calculate` tool. It refuses any input number that never appeared in an earlier observation.
- **Revision.** When either check finds problems, the synthesizer gets the issue list and rewrites the answer once. The better of the two versions is kept.

## Data

| ID | Table | What | Real? |
|---|---|---|---|
| D-OCEAN | `ocean_monthly` | Monthly SST, chlorophyll-a, salinity and dissolved oxygen for 6 regions, 2004–2024 | **Synthetic** |
| D-FISH | `fish_landings` | Annual landings by state and species, 2004–2024 | **Synthetic** |
| D-OCC | `species_occurrence` | Survey counts and eDNA reads for protected and key species | **Synthetic** |
| D-CORAL | `coral_reef_monitoring` | Coral cover, % bleached and max DHW at 6 reef sites | **Synthetic** |
| D-SPECIES | `species_reference` | Family, habitat, IUCN category and WoRMS AphiaID | Curated (families and AphiaIDs checked against OBIS/WoRMS) |
| live | OBIS API | Occurrence records and contributing datasets | **Real**, queried live |
| notes | `data/knowledge/*.md` | NOAA Coral Reef Watch DHW levels, Hobday et al. 2016 heatwaves, IPCC SROCC, Roxy et al. 2014, monsoon upwelling, species notes | Cited background notes |

The synthetic tables come from `orca/data/generate.py` (seed 176). They encode documented patterns: the southwest-monsoon chlorophyll bloom off Kerala, warm years (2010, 2016, 2024), oil sardine landings lagging upwelling by a year, and bleaching driven by DHW. The agents have something real to reason about, but **the numbers are not observations**. Every answer that cites them says so, and the UI flags it.

### Loading real data

```bash
python -m orca.data.ingest landings.csv --table fish_landings --dataset-id D-FISH --replace \
  --title "Marine fish landings by state" \
  --citation "ICAR-CMFRI (2024). Marine Fish Landings in India 2023." \
  --url https://www.cmfri.org.in --describe landings_tonnes="landings in tonnes"
```

The CSV header becomes the column names. The citation you pass appears in every answer that uses the table. Restart the server to pick up catalog changes.

## Run locally

Prerequisites: Python 3.11+ and Ollama. All commands run from this `code/` folder.

```bash
ollama pull qwen3:8b
python -m venv .venv && .venv/Scripts/activate      # Windows; use bin/activate elsewhere
pip install -r requirements.txt
python -m orca.data.generate                        # builds data/orca_demo.sqlite
python -m orca.cli check                            # Ollama reachable + model pulled?
streamlit run streamlit_app.py                      # Streamlit app, or:
uvicorn orca.api.app:create_app --factory --port 8000
```

## Deploy the Streamlit app (Streamlit Community Cloud)

Streamlit Cloud has no GPU, so it cannot run the model itself. The app runs the agents there and calls an
Ollama server you point it at; without one, it still serves the recorded test runs.

1. Push the repository to GitHub (public).
2. On share.streamlit.io: **Create app** → pick the repo, branch `main`, main file `code/streamlit_app.py`,
   and under **Advanced settings** choose Python 3.11.
3. In **Advanced settings → Secrets**, add:
   ```toml
   ORCA_OLLAMA_BASE_URL = "https://<your-tunnel-host>"
   ORCA_MODEL = "qwen3:8b"
   ```
4. To make live mode work, expose the Ollama on your machine through a tunnel while you want it online:
   ```bash
   cloudflared tunnel --url http://localhost:11434 --http-host-header="localhost:11434"
   ```
   Put the printed `https://....trycloudflare.com` URL in the secret. Quick-tunnel URLs change on every
   restart; a named Cloudflare tunnel or a static ngrok domain keeps it fixed. Anyone with the URL can use
   that Ollama server, so stop the tunnel when you don't need it.

Open http://localhost:8000. You can also skip the browser:

```bash
python -m orca.cli ask "Did weak monsoon upwelling off Kerala affect oil sardine landings in the following year?"
```

## Deploy with Docker

```bash
cp .env.example .env            # optional: set ORCA_API_KEY etc.
docker compose up -d --build    # CPU
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build   # NVIDIA GPU
```

Compose starts three services:
- `ollama`: the model server. It stays inside the compose network and isn't published to the host.
- `ollama-pull`: a one-shot job that downloads `ORCA_MODEL` into a volume and exits.
- `orca`: the API and UI on port 8000. It runs as a non-root user on a read-only root filesystem, and run history lives in a named volume.

The `orca` service waits until the model pull has finished. The first start downloads about 5 GB.

Production notes:
- Set `ORCA_API_KEY` to require an `X-API-Key` header on `/api/ask`.
- `/api/health` is the liveness probe. `/api/ready` returns 503 until Ollama is reachable and the model is pulled.
- One GPU serves one run well. `ORCA_MAX_CONCURRENT_RUNS=1` queues the rest, and past `ORCA_MAX_QUEUED_RUNS` the API returns 429.
- `/api/ask` streams Server-Sent Events. Behind nginx, set `proxy_buffering off;` and `proxy_read_timeout 600s;` for that location.
- To turn off the live OBIS tool on air-gapped hosts, set `ORCA_ENABLE_OBIS=false`.

## API

| Method | Path | |
|---|---|---|
| POST | `/api/ask` | `{"question": "..."}`. Streams SSE events (`plan`, `step_started`, `thought`, `action`, `observation`, `draft`, `verification`, `final`). Add `?stream=false` to get JSON back instead. |
| GET | `/api/runs`, `/api/runs/{id}` | Run history and full stored results (plan, trace, evidence, verification) |
| GET | `/api/datasets`, `/api/datasets/{id}` | Catalog with citations |
| GET | `/api/health`, `/api/ready`, `/api/config` | Probes and UI config |

## Configuration

All settings are environment variables with the `ORCA_` prefix; see `.env.example`. The ones you're most likely to change:
- `ORCA_MODEL`: the model for every agent. You can override it per role with `ORCA_PLANNER_MODEL`, `ORCA_SPECIALIST_MODEL` and `ORCA_SYNTHESIZER_MODEL`.
- `ORCA_THINK`: turns qwen3's thinking mode on or off. It's off by default.
- `ORCA_MAX_REACT_ITERATIONS`, `ORCA_MAX_REVISIONS` and `ORCA_LLM_CRITIC`: agent limits and checks.

## Tests

```bash
pip install pytest pytest-asyncio ruff
pytest -q          # 48 tests, offline: a scripted LLM stands in for Ollama
ruff check orca tests scripts
python scripts/run_tests.py --model qwen3:8b --out ../test-data/outputs/my-run   # live test table
```

The tests cover:
- the statistics, against hand-worked values
- the read-only SQL guard
- every tool
- the verifier's matching rules
- the ReAct parser and loop, including bad JSON, repeated failing calls and running out of iterations
- the orchestrator's revision path
- the API (SSE stream, auth, error handling)
- CSV ingest

CI (`.github/workflows/ci.yml`) runs lint and tests, builds the image and smoke-tests it.

## Layout

```
orca/
  agents/      react.py (ReAct loop), roles.py (planner + specialists),
               synthesizer.py (answer + critic), verifier.py (number check)
  tools/       data.py (catalog, query_data, run_sql, species_lookup),
               analysis.py (trend, correlate, anomaly, calculate),
               external.py (search_knowledge, obis_occurrences)
  data/        generate.py (demo DB), ingest.py (real CSVs), store.py (read-only SQLite)
  orchestrator.py, evidence.py, stats.py, knowledge.py, llm.py (Ollama client), runs.py
  api/app.py   FastAPI + SSE        web/   single-page UI (no build step)
data/knowledge/   cited background notes
```

## Known limits

- An 8B local model sometimes plans more steps than a question needs, or misses a filter. The verifier catches wrong numbers. It can't catch every wrong interpretation (for example, "2020 had the most heatwave months" when 2024 tied it), and the critic is a second small-model opinion, not ground truth. Read the evidence panel for anything that matters.
- The marine-heatwave and DHW tools work on monthly data, so they approximate the daily-data definitions (Hobday et al. 2016; NOAA CRW). The tool output says so.
- Expect 20–70 s per question on an 8 GB GPU with `qwen3:8b`. CPU-only runs are much slower.
