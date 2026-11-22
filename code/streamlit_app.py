"""ORCA-Prompt Streamlit app.

Two modes:
- Ask live: runs the multi-agent pipeline against an Ollama server
  (ORCA_OLLAMA_BASE_URL, set in Streamlit secrets or the environment).
- Saved test runs: browses the recorded, real test-table runs in
  test-data/outputs, so the app is useful even when no model server is online.

Run locally:  streamlit run code/streamlit_app.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import streamlit as st

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

st.set_page_config(page_title="ORCA-Prompt", page_icon="🐋", layout="wide")


def _load_secrets_into_env() -> None:
    """Streamlit secrets -> ORCA_* environment variables (read by Settings)."""
    try:
        for key, value in st.secrets.items():
            if key.startswith("ORCA_") and isinstance(value, (str, int, float, bool)):
                os.environ.setdefault(key, str(value))
    except Exception:  # noqa: BLE001,S110 - no secrets file locally is fine
        pass


_load_secrets_into_env()

import httpx  # noqa: E402

from orca.config import Settings  # noqa: E402
from orca.data import generate  # noqa: E402
from orca.data.store import MarineStore  # noqa: E402
from orca.knowledge import KnowledgeBase  # noqa: E402
from orca.llm import LLMError, OllamaLLM  # noqa: E402
from orca.orchestrator import Orchestrator  # noqa: E402

SAVED_RUNS_DIR = REPO_ROOT / "test-data" / "outputs" / "run-v3-qwen3-8b"
EXAMPLES = [
    "Did weak monsoon upwelling off Kerala affect oil sardine landings in the following year?",
    "How severe was coral heat stress at Lakshadweep reefs in 2016 and 2024, and what do the DHW values mean for bleaching?",
    "Is sea surface temperature in the Andaman Sea rising, and how fast per decade?",
    "Which months after 2013 had marine-heatwave-like SST in the Lakshadweep Sea?",
    "How have olive ridley nesting counts in the NW Bay of Bengal changed, and what is the species' IUCN status?",
    "How many OBIS occurrence records exist for whale sharks in the NE Arabian Sea since 2000?",
]


@st.cache_resource
def resources() -> tuple[Settings, MarineStore, KnowledgeBase]:
    settings = Settings(_env_file=None)
    if not Path(settings.data_db).exists():
        generate.build(Path(settings.data_db))
    return settings, MarineStore(settings.data_db, row_limit=settings.sql_row_limit), KnowledgeBase(settings.knowledge_dir)


@st.cache_data(ttl=30, show_spinner=False)
def model_status(base_url: str, model: str) -> dict:
    async def _check():
        llm = OllamaLLM(base_url)
        try:
            return await llm.health(model)
        finally:
            await llm.aclose()

    return asyncio.run(_check())


@st.cache_data(show_spinner=False)
def saved_runs() -> list[dict]:
    if not SAVED_RUNS_DIR.exists():
        return []
    review_path = REPO_ROOT / "test-data" / "review.json"
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    runs = []
    for p in sorted(SAVED_RUNS_DIR.glob("T*.json")):
        run = json.loads(p.read_text(encoding="utf-8"))
        tid = run["test"]["id"]
        if tid in review:  # hand review overrides the keyword check
            run["pass"] = review[tid]["pass"]
            run["problems"] = [*run["problems"], review[tid]["note"]]
        runs.append(run)
    return runs


# ---------------------------------------------------------------- rendering
def cite_badges(text: str) -> str:
    return re.sub(r"\[(E\d+(?:\s*[,;]\s*E\d+)*)\]", lambda m: f"`[{m.group(1)}]`", text)


def render_result(result: dict) -> None:
    v = result["verification"]
    critic = v.get("critic_problems") or []
    if v["passed"] and not critic:
        st.success(f"Verified: {v['verified_claims']}/{v['numeric_claims']} numeric claims match the cited evidence.")
    elif v["passed"]:
        st.warning(f"Numbers verified ({v['verified_claims']}/{v['numeric_claims']}); critic agent left {len(critic)} note(s).")
    else:
        st.error(f"Unverified claims: {v['verified_claims']}/{v['numeric_claims']} numeric claims matched the evidence.")
    if result.get("uses_synthetic_data"):
        st.caption("⚠️ Cites ORCA's synthetic demo datasets. They show how the method works; they are not real observations.")

    st.markdown(cite_badges(result["answer"]))

    tab_refs, tab_ver, tab_trace, tab_ev = st.tabs(["References", "Verification", "Agent trace", "Evidence"])
    with tab_refs:
        for ref in result.get("references", []):
            cites = "; ".join(
                s["citation"] + (f" ({s['url']})" if s.get("url") else "") + (" **[SYNTHETIC]**" if s.get("synthetic") else "")
                for s in ref["sources"]
            ) or "computed"
            st.markdown(f"**{ref['id']}** · `{ref['tool']}` · {cites}")
            st.code(json.dumps(ref["args"], ensure_ascii=False), language="json")
    with tab_ver:
        st.write(f"Score {v['score']} · revisions {result.get('revisions', 0)} · {result.get('elapsed_s', '?')} s")
        for c in v.get("claims", []):
            if c["status"] == "no_numbers":
                continue
            icon = {"verified": "✅", "miscited": "🟠", "uncited": "🟠"}.get(c["status"], "❌")
            st.markdown(f"{icon} `{c['status']}` {c['text']}")
        for issue in v.get("issues", []):
            st.markdown(f"- {issue}")
        for p in critic:
            st.markdown(f"- Critic: {p['problem']} _(claim: {p['claim']})_")
    with tab_trace:
        st.markdown("**Plan** (" + result.get("plan_source", "") + ")")
        for i, s in enumerate(result["plan"], 1):
            st.markdown(f"{i}. `{s['specialist']}` {s['task']}")
        for step in result.get("steps", []):
            with st.expander(f"{step['step_id']} · {step['title']}", expanded=False):
                st.caption(step["task"])
                for t in step.get("trace", []):
                    if t.get("thought"):
                        st.markdown(f"**Thought:** {t['thought']}")
                    if t.get("action"):
                        st.markdown(f"**Action:** `{t['action']}`")
                        st.code(json.dumps(t.get("action_input") or {}, ensure_ascii=False), language="json")
                    if t.get("observation"):
                        st.code(t["observation"], language="text")
                st.markdown(f"**Findings:** {cite_badges(step['findings'])}")
    with tab_ev:
        for ev in result.get("evidence", []):
            synthetic = any(s.get("synthetic") for s in ev["sources"])
            with st.expander(f"{ev['id']} · {ev['tool']}" + (" · SYNTHETIC" if synthetic else "")):
                st.code(ev["text"], language="text")


# ---------------------------------------------------------------- live run
async def run_live(question: str, settings: Settings, store: MarineStore, kb: KnowledgeBase, status) -> dict:
    llm = OllamaLLM(settings.ollama_base_url, temperature=settings.temperature, num_ctx=settings.num_ctx,
                    num_thread=settings.num_thread, think=settings.think, timeout_s=settings.llm_timeout_s,
                    keep_alive=settings.keep_alive)
    async with httpx.AsyncClient(timeout=settings.obis_timeout_s) as http:
        orch = Orchestrator(settings, llm, store, kb, http)

        async def emit(kind: str, p: dict) -> None:
            if kind == "plan":
                status.write("**Plan:** " + " → ".join(f"{s['specialist']}" for s in p["steps"]))
            elif kind == "step_started":
                status.update(label=f"{p['step_id']} · {p['title']} working…")
                status.write(f"**{p['step_id']} {p['title']}:** {p['task']}")
            elif kind == "thought" and p.get("text"):
                status.write(f"💭 _{p['agent']}_: {p['text'][:300]}")
            elif kind == "action":
                status.write(f"🔧 `{p['tool']}` {json.dumps(p['input'], ensure_ascii=False)[:200]}")
            elif kind == "observation":
                tag = "⚠️ " if p["error"] else "📄 "
                status.write(f"{tag}{p['evidence_id'] or ''} {p['text'][:200]}")
            elif kind == "phase":
                status.update(label=f"{p['phase']}…")
            elif kind == "verification":
                status.write(f"🔎 verifier: passed={p['passed']} score={p['score']}")

        try:
            return await orch.run(question, emit)
        finally:
            await llm.aclose()


# ---------------------------------------------------------------- page
settings, store, kb = resources()
st.title("🐋 ORCA-Prompt")
st.caption("Multi-agent ReAct assistant for marine ecosystem reasoning · planner + specialist agents · "
           "data-verified, cited answers · local LLM via Ollama")

qp = st.query_params
with st.sidebar:
    mode = st.radio("Mode", ["Ask live", "Saved test runs"], index=1 if qp.get("mode") == "saved" or qp.get("test") else 0)
    health = model_status(settings.ollama_base_url, settings.model)
    live_ok = bool(health.get("reachable") and health.get("model_available"))
    if live_ok:
        st.success(f"Model online: {settings.model}")
    else:
        st.warning("Model server offline. Live questions are unavailable right now; "
                   "the saved test runs show real recorded outputs.")
    st.divider()
    st.markdown("**Datasets**")
    for d in store.datasets:
        st.markdown(f"- `{d.dataset_id}` {d.title}" + (" _(synthetic)_" if d.synthetic else ""))
    st.caption("OBIS records are fetched live from api.obis.org.")

if mode == "Ask live":
    example = st.selectbox("Example questions", ["(write your own)"] + EXAMPLES)
    question = st.text_area("Question", value="" if example.startswith("(") else example, height=90,
                            max_chars=1000, placeholder="Ask about SST, chlorophyll, fisheries, coral, protected species…")
    go = st.button("Ask the agents", type="primary", disabled=not live_ok)
    if not live_ok:
        st.info("Live mode needs the Ollama model server, which is offline at the moment. "
                "Switch to **Saved test runs** in the sidebar to see recorded runs of the same system.")
    if go:
        q = question.strip()
        if len(q) < 3:
            st.error("Please enter a question of at least 3 characters.")
        else:
            with st.status("Planning…", expanded=True) as status:
                try:
                    result = asyncio.run(run_live(q, settings, store, kb, status))
                    status.update(label=f"Done in {result['elapsed_s']} s", state="complete", expanded=False)
                except LLMError as exc:
                    status.update(label="Model server error", state="error")
                    st.error(str(exc))
                    result = None
            if result:
                st.session_state["last_result"] = result
    if st.session_state.get("last_result"):
        st.subheader("Answer")
        render_result(st.session_state["last_result"])
else:
    runs = saved_runs()
    if not runs:
        st.warning("No saved runs found in test-data/outputs.")
    else:
        passed = sum(r["pass"] for r in runs)
        st.markdown(f"Recorded test-table run with **{runs[0]['llm_calls'][0]['model'] if runs[0]['llm_calls'] else 'qwen3:8b'}**: "
                    f"{passed}/{len(runs)} tests passed. Each is a real run; nothing here is edited.")
        labels = [f"{r['test']['id']} · {r['test']['type']} · {'Pass' if r['pass'] else 'Fail'} · {r['test']['question'][:70]}"
                  for r in runs]
        ids = [r["test"]["id"] for r in runs]
        start = ids.index(qp["test"]) if qp.get("test") in ids else 0
        pick = st.selectbox("Test", range(len(runs)), index=start, format_func=lambda i: labels[i])
        r = runs[pick]
        st.markdown(f"**Question:** {r['test']['question']!r}")
        st.markdown(f"**Expected:** {r['test']['expected']}")
        st.markdown(f"**Result:** {'✅ Pass' if r['pass'] else '❌ Fail'}"
                    + (f" ({'; '.join(r['problems'])})" if r["problems"] else "")
                    + f" · {r['seconds']} s · {len(r['llm_calls'])} LLM calls")
        if r["status"] == 200:
            render_result(r["result"])
        else:
            st.code(json.dumps(r["result"], indent=2), language="json")
            st.caption(f"HTTP {r['status']}: rejected by input validation before any agent ran.")
