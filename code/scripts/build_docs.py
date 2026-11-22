"""Build the Milestone 2 PDF, the supporting technical report and docs/test-table.md.

    python scripts/build_docs.py

Inputs: docs/team.json (team details and links), prompts/, docs/diagrams/*.png,
docs/prompt-iteration-log.md, test-data/outputs/run-v3-qwen3-8b/summary.json,
docs/screenshots/*.png. PDFs are printed with headless Microsoft Edge.
"""

from __future__ import annotations

import base64
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
RUN_DIR = ROOT / "test-data" / "outputs" / "run-v3-qwen3-8b"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

CSS = """
@page { size: A4; margin: 16mm 15mm; }
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 10.5pt; line-height: 1.45; color: #111; }
h1 { font-size: 20pt; margin: 0 0 6pt; } h2 { font-size: 15pt; border-bottom: 2px solid #136f7a; padding-bottom: 3pt; margin-top: 18pt; }
h3 { font-size: 12pt; margin: 14pt 0 4pt; color: #0d4f57; } h4 { font-size: 11pt; margin: 10pt 0 4pt; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0; font-size: 9pt; page-break-inside: auto; }
th, td { border: 1px solid #999; padding: 3pt 5pt; vertical-align: top; text-align: left; }
th { background: #e6f0f1; } tr { page-break-inside: avoid; }
pre { white-space: pre-wrap; word-break: break-word; background: #f4f4f4; border: 1px solid #ddd; padding: 6pt; font-size: 8pt; line-height: 1.35; }
code { font-family: Consolas, monospace; font-size: 8.5pt; }
img { max-width: 100%; border: 1px solid #ccc; } figure { margin: 8pt 0; page-break-inside: avoid; } figcaption { font-size: 9pt; color: #444; }
.section { page-break-before: always; } .cover td:first-child { width: 28%; font-weight: 600; }
.small { font-size: 9pt; color: #444; } .pass { color: #1d6b3a; font-weight: 600; } .fail { color: #a32a1f; font-weight: 600; }
"""

MODULES = [
    ("Planner agent (P1, P6)", "Turns the question into a JSON plan of 1-4 specialist steps; JSON repair fallback", "Working",
     "One mapping error seen (T09: oil sardine → mackerel)"),
    ("Specialist agents (P2, 4 roles)", "ReAct loop over data tools for oceanography, fisheries, biodiversity, context", "Working",
     "Max 6 tool calls per step; nudged once if it answers without a tool"),
    ("Data tools", "Catalog, structured query, read-only SQL, species lookup", "Working", "SQL is SELECT-only, enforced by an SQLite authorizer"),
    ("Analysis tools", "Trend (OLS, Mann-Kendall, Sen), lagged correlation, anomaly / monthly heatwave, traced calculator", "Working",
     "Monthly heatwave and DHW are monthly approximations of daily methods"),
    ("Knowledge search", "BM25 search over cited background notes (NOAA CRW, IPCC, Hobday 2016 ...)", "Working", "10 notes, 33 passages"),
    ("Live OBIS tool", "Real occurrence counts and dataset citations from api.obis.org", "Working", "Needs internet; counts reflect effort, not abundance"),
    ("Synthesizer (P3)", "Writes the Markdown answer with [E#] citations", "Working", ""),
    ("Number verifier", "Checks every number in the answer against the cited evidence (no LLM)", "Working",
     "Cannot catch a real number attached to the wrong entity (T09)"),
    ("Critic + revision (P5, P4)", "LLM check of claim support; one automatic rewrite", "Partly working",
     "Missed the T09 species mix-up; earlier versions gave false positives"),
    ("Streamlit app", "Ask live + saved test runs, agent trace, evidence, verification", "Working",
     "Live mode needs the team's Ollama server online (Streamlit Cloud has no GPU)"),
    ("FastAPI service + web UI", "Streaming API, run history, health checks, API key", "Working", "Used for local and Docker deployment"),
    ("Docker / Compose deployment", "Image with Ollama service and model pull", "Partly working",
     "Image built and smoke-tested; full Compose with the Ollama container not yet run"),
    ("Real-data ingest", "Load a CSV with its citation into the catalog", "Working", "Tested by unit test; no real dataset loaded yet"),
]


def md(text: str) -> str:
    return markdown.markdown(text, extensions=["tables", "fenced_code"])


def img_tag(path: Path, caption: str) -> str:
    if not path.exists():
        return f"<p class='small'>[missing image: {html.escape(str(path.name))}]</p>"
    data = base64.b64encode(path.read_bytes()).decode()
    return f"<figure><img src='data:image/png;base64,{data}'><figcaption>{html.escape(caption)}</figcaption></figure>"


def load_runs() -> tuple[list[dict], dict[str, dict]]:
    rows = json.loads((RUN_DIR / "summary.json").read_text(encoding="utf-8"))
    full = {r["id"]: json.loads((RUN_DIR / f"{r['id']}.json").read_text(encoding="utf-8")) for r in rows}
    review = json.loads((ROOT / "test-data" / "review.json").read_text(encoding="utf-8"))
    for r in rows:
        r["auto_pass"] = r["pass"]
        if r["id"] in review:
            r["pass"] = review[r["id"]]["pass"]
            r["problems"] = [*r["problems"], review[r["id"]]["note"]]
    return rows, full


def test_table_md(rows: list[dict], full: dict[str, dict]) -> str:
    lines = ["| Test no. | Input (short) | Type | Expected output | Actual output (short) | Pass / Fail |", "|---|---|---|---|---|---|"]
    for r in rows:
        note = f" — {'; '.join(r['problems'])}" if r["problems"] else ""
        lines.append(f"| {r['id']} | {r['question'].strip() or '(only spaces)'} | {r['type']} | {r['expected']} | "
                     f"{r['actual'].replace('|', '/')}{note} | {'Pass' if r['pass'] else 'Fail'} |")
    passed = sum(r["pass"] for r in rows)
    kinds = {k: sum(1 for r in rows if r["type"] == k) for k in ("Normal", "Edge", "Invalid")}
    sources: dict[str, int] = {}
    revisions = critic = 0
    for f in full.values():
        res = f["result"]
        if f["status"] == 200:
            sources[res["plan_source"]] = sources.get(res["plan_source"], 0) + 1
            revisions += res["revisions"]
            critic += len(res["verification"].get("critic_problems") or [])
    secs = [r["seconds"] for r in rows if r["llm_calls"]]
    stats = (f"**{passed}/{len(rows)} passed** ({kinds['Normal']} normal, {kinds['Edge']} edge, {kinds['Invalid']} invalid). "
             f"Model qwen3:8b on an RTX 5050 laptop GPU. Time per answered question: {min(secs)}-{max(secs)} s "
             f"(median {sorted(secs)[len(secs) // 2]} s). Plan source: {sources}. Automatic revisions: {revisions}. "
             f"Critic notes left in final answers: {critic}. Pass/Fail: automatic checks from "
             f"`test-data/inputs.json` passed {sum(r['auto_pass'] for r in rows)}/{len(rows)}; a reviewer then read every "
             f"answer and overrode {sum(r['auto_pass'] and not r['pass'] for r in rows)} wrong passes "
             "(`test-data/review.json`). Full outputs and every LLM call are in `test-data/outputs/run-v3-qwen3-8b/`.")
    return stats + "\n\n" + "\n".join(lines)


def version_texts() -> str:
    out = []
    for name, label in (("P1_planner", "P1 Planner"), ("P2_specialist_oceanography", "P2 Specialist (oceanography role shown)"),
                        ("P3_synthesizer", "P3 Synthesizer"), ("P5_critic", "P5 Critic")):
        for v in (1, 2, 3):
            text = (ROOT / "prompts" / f"v{v}" / f"{name}.txt").read_text(encoding="utf-8")
            out.append(f"<h4>{label} · v{v}</h4><pre>{html.escape(text)}</pre>")
    return "\n".join(out)


def technical_report(team: dict, rows: list[dict]) -> str:
    passed = sum(r["pass"] for r in rows)
    return f"""
<h1>Technical report: ORCA-Prompt</h1>
<p class="small">{html.escape(team['team_name'])} · {html.escape(team['project_title'])}</p>
<h3>Architecture</h3>
<p>A question passes through four steps. The <b>planner</b> (P1) writes a JSON plan of 1-4 steps. Each step goes to a
<b>specialist</b> ReAct agent (P2: oceanography, fisheries, biodiversity or context) that calls data tools; every
data-bearing tool result is stored in an evidence ledger as E1, E2, ... with its query and source citation. The
<b>synthesizer</b> (P3) writes a Markdown answer citing those ids. A deterministic <b>verifier</b> matches every
number in the answer against the cited evidence, and a <b>critic</b> (P5) checks claim support; if either finds
problems, one <b>revision</b> (P4) rewrites the answer. Front ends: a Streamlit app and a FastAPI web UI.</p>
<h3>Tools and models</h3>
<p>All six prompts run on <b>qwen3:8b</b> through a local <b>Ollama</b> server (temperature 0.1, 8k context, thinking off),
chosen because it fits an 8 GB laptop GPU and follows the ReAct text format and JSON schemas. Smaller models were
tested for a CPU host: qwen3:1.7b took 549 s for one question on 2 CPU threads and answered wrongly. Eleven tools
cover a read-only SQLite marine database (structured query, SQL, trend with Mann-Kendall test, lagged correlation,
anomaly and monthly heatwave detection, traced calculator, species lookup), BM25 search over cited notes
(NOAA Coral Reef Watch, IPCC SROCC, Hobday et al. 2016) and live OBIS occurrence records.</p>
<h3>Prompt design</h3>
<p>ReAct prompts use plain-text Thought / Action / Action Input turns with a stop sequence before "Observation", so any
local model can follow them and the trace stays readable. Each prompt states strict rules: copy numbers from
observations, cite evidence ids, use the calculator for derived numbers, say when data cannot answer. Three
versions of each main prompt were driven by failures in real runs (see the iteration log).</p>
<h3>Test results</h3>
<p>{passed}/{len(rows)} test inputs passed after hand review (7 normal, 5 edge, 3 invalid). The system refused an injected
false number (T11), handled a vague question (T10), an out-of-region question (T08) and an off-topic question (T15),
and rejected invalid input (T13, T14). It failed four: T09 (Hinglish; planner mapped "oil sardine" to mackerel),
T01 (picked the wrong region in this run), T04 (lead paragraph merged months into wrong ranges) and T12 (invented a
2030 estimate). In T01 and T09 the number check passed numbers that were real evidence for the wrong entity.
48 offline unit tests cover tools, verifier, agents and API.</p>
<h3>Limitations</h3>
<ul><li>Four of the five local tables are synthetic demo data (clearly labelled); real CMFRI/INCOIS/OBIS data must be
loaded before use in decisions.</li>
<li>The number verifier cannot tell when a correct number is attached to the wrong entity; the critic catches some of
these, not all.</li>
<li>Streamlit Community Cloud has no GPU, so live answers need the team's Ollama server to be reachable.</li></ul>
"""


def build() -> None:
    team = json.loads((DOCS / "team.json").read_text(encoding="utf-8"))
    rows, full = load_runs()
    n_prompts = len(list((ROOT / "prompts" / "final").glob("P*.md")))
    n_versions = sum(len(list((ROOT / "prompts" / f"v{v}").glob("*.txt"))) for v in (1, 2, 3))
    table = test_table_md(rows, full)
    (DOCS / "test-table.md").write_text("# Test table (final prompts, v3)\n\n" + table + "\n", encoding="utf-8")

    members_rows = "".join(f"<tr><td>{html.escape(m['name'])}</td><td>{html.escape(m['enrollment'])}</td></tr>" for m in team["members"])
    L = team["links"]
    cover = f"""
<h1>{html.escape(team['project_title'])}</h1>
<p>SCSE4041 Mini Project · Milestone 2</p>
<table class="cover">
<tr><td>Team ID</td><td>{html.escape(team['team_id'])}</td></tr>
<tr><td>Team name</td><td>{html.escape(team['team_name'])}</td></tr>
<tr><td>Project title</td><td>{html.escape(team['project_title'])}</td></tr>
<tr><td>Batch</td><td>{html.escape(team['batch'])}</td></tr>
<tr><td>Faculty</td><td>{html.escape(team['faculty'])}</td></tr>
<tr><td>Prototype link</td><td>{html.escape(L['prototype'])}</td></tr>
<tr><td>Repository link</td><td>{html.escape(L['repository'])}</td></tr>
<tr><td>Reel link</td><td>{html.escape(L['reel'])} ({html.escape(str(team['reel_seconds']))} s)</td></tr>
</table>
<h3>Members</h3><table><tr><th>Name</th><th>Enrollment no.</th></tr>{members_rows}</table>
"""
    design = f"""
<h3>(a) Design summary</h3>
<p><b>Problem and users.</b> Marine scientists, fisheries officers and conservation managers (the users behind problem
statement SIH26176) need answers that combine ocean, fisheries and biodiversity data. General chatbots answer such
questions fluently but invent numbers and cite nothing. ORCA-Prompt answers in plain language and ties every number
to a data query and a citation, then checks it.</p>
<p><b>Input and output.</b> Input: one free-text question (3-1000 characters). Output: a Markdown answer (direct answer,
key findings, caveats) with evidence ids such as [E2]; a reference list with the exact query and source citation for
each id; a verification report with the status of every numeric claim; and the full agent trace.</p>
<p><b>Prompts and steps ({n_prompts} prompts, 4 steps).</b> Plan (P1 planner, P6 JSON repair fallback) → act (P2 specialist,
one template in 4 roles) → answer (P3 synthesizer) → check (deterministic verifier + P5 critic, P4 revision when
needed). One prompt cannot plan, run tools and check itself reliably on an 8B model, so each step has a short,
focused prompt, and the checks sit between the draft and the final answer.</p>
<p><b>Tool and model.</b> qwen3:8b through a local Ollama server on the team laptop's RTX 5050 (8 GB): free, private, fits
the GPU, and follows the ReAct text format and JSON schemas. We measured smaller models for a CPU host: qwen3:1.7b
took 549 s for one question on 2 CPU threads and answered wrongly; qwen3:4b took 310 s for one test even on GPU.</p>
<p><b>Changes since Milestone 1.</b> {html.escape(team['milestone1_summary'])} Since then we built the full multi-agent
pipeline, the tools and verifier, ran three prompt iterations against real failures and added the Streamlit app.</p>
<h3>(b) Diagrams</h3>
{img_tag(DOCS / 'diagrams' / '1-prompt-workflow.png', 'Figure 1. Prompt workflow')}
{img_tag(DOCS / 'diagrams' / '2-task-decomposition-example-t01.png', 'Figure 2. Task decomposition (test T01)')}
{img_tag(DOCS / 'diagrams' / '3-input-output-flow.png', 'Figure 3. Input-output flow')}
{img_tag(DOCS / 'diagrams' / '4-system-architecture.png', 'Figure 4. System architecture')}
{img_tag(DOCS / 'diagrams' / '5-agent-loop-react-used-by-p1-and-p2.png', 'Figure 5. ReAct agent loop (P1, P2)')}
<h3>(c) Prompt sheets</h3>
""" + "".join(f"<div class='section'>{md(p.read_text(encoding='utf-8'))}</div>" for p in sorted((ROOT / 'prompts' / 'final').glob('P*.md')))

    mod_rows = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>" for a, b, c, d in MODULES)
    log = (DOCS / "prompt-iteration-log.md").read_text(encoding="utf-8")
    log = re.sub(r"^# .*\n", "", log)
    impl = f"""
<h3>What is built</h3>
<p>A Python package (<code>code/orca</code>) with the agents, eleven tools, evidence ledger, verifier, FastAPI service and web UI;
a Streamlit app; a demo marine database with a citation catalog; 48 offline tests; Docker and CI files.</p>
<h3>Module status</h3>
<table><tr><th>Module</th><th>What it does</th><th>Status</th><th>Notes</th></tr>{mod_rows}</table>
<h3>Prompt iteration log</h3>
{md(log)}
<h3>Full text of each version</h3>
{version_texts()}
"""
    shots = [
        ("T02-saved-run.png", "Screenshot 1 (test T02, Pass): Andaman Sea SST trend with verification badge and evidence citations"),
        ("T03-saved-run.png", "Screenshot 2 (test T03): coral heat stress answer with DHW alert levels"),
        ("T09-saved-run.png", "Screenshot 3 (test T09, Fail): Hinglish question answered with the wrong species"),
    ]
    testing = f"""
<h3>Test table</h3>
{md(table)}
<h3>Screenshots of real runs</h3>
{''.join(img_tag(DOCS / 'screenshots' / f, c) for f, c in shots)}
<h3>Known limitations and what we will try next</h3>
<ol>
<li><b>Right number, wrong entity.</b> T09 mapped "oil sardine" to <i>Rastrelliger kanagurta</i> (mackerel) because the planner
sees scientific names only, and T01 used the wrong region. The number verifier passed both because the numbers were
real evidence for a different species or region. Next: add common names to the data guide, and check that the
filters in each cited query (species, region, site, year) match the entities named in the claim.</li>
<li><b>Unstable answers and invented projections.</b> T01 was answered correctly in earlier runs but wrongly in the test run
(wrong region), and T12 produced a 2030 "estimate" from a trend. Next: run each step twice and flag disagreements,
and add a verifier rule that rejects years outside the data range unless the calculate tool produced the number.</li>
<li><b>Synthetic data.</b> Ocean, landings, survey and coral tables are synthetic demo data. Next: load real ICAR-CMFRI landings,
INCOIS/NOAA SST and OBIS data with the ingest tool.</li>
<li><b>Online live mode needs our GPU.</b> Streamlit Cloud cannot run the model, so live answers work only while our Ollama
server is reachable; saved runs are always available. Next: a GPU host for Milestone 3.</li>
</ol>
"""
    repo = f"""
{img_tag(DOCS / 'screenshots' / 'repository.png', 'Repository screenshot')}
<ul>
<li>Restructured into prompts/ (v1, v2, v3, final), code/, test-data/ and docs/ with a dated CHANGELOG.md.</li>
<li>Built the multi-agent pipeline (6 prompts), 11 tools, evidence ledger, verifier and critic.</li>
<li>Added the Streamlit app with live and saved-run modes, plus FastAPI, Docker and CI.</li>
<li>Ran 15 test inputs on qwen3:8b and saved every output and LLM call in test-data/outputs/.</li>
<li>Three prompt versions for planner, specialist, synthesizer and critic, each driven by a failed run.</li>
</ul>
"""
    contrib_rows = "".join(
        f"<tr><td>{html.escape(m['name'])}</td><td>{html.escape(m['enrollment'])}</td><td>{html.escape(m['role'])}</td>"
        f"<td>{html.escape(m['tasks'])}</td><td>{html.escape(m['evidence'])}</td><td>{m['percent']}</td></tr>" for m in team["members"])
    total = sum(m["percent"] for m in team["members"])
    contrib = (f"<table><tr><th>Member name</th><th>Enrollment no.</th><th>Role</th><th>Tasks done in Milestone 2</th>"
               f"<th>Evidence</th><th>Contribution (%)</th></tr>{contrib_rows}</table><p class='small'>Total: {total}%</p>")

    sections = [("1. Cover page", cover), ("2. Prompt and solution design", design), ("3. Implementation", impl),
                ("4. Demonstration and testing", testing), ("5. Repository", repo),
                ("6. Supporting document", technical_report(team, rows)), ("7. Team contribution", contrib)]
    body = "".join(f"<div class='{'section' if i else ''}'><h2>{t}</h2>{c}</div>" for i, (t, c) in enumerate(sections))
    out_dir = DOCS / "milestone2"
    out_dir.mkdir(exist_ok=True)
    m2_name = f"M2_{team['team_id']}_{team['project_title_short']}"
    sup_name = f"{team['team_name']}_TechnicalReport"
    pages = {m2_name: body, sup_name: technical_report(team, rows)}
    for name, content in pages.items():
        page = out_dir / f"{name}.html"
        page.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>{name}</title><style>{CSS}</style>"
                        f"</head><body>{content}</body></html>", encoding="utf-8")
        pdf = out_dir / f"{name}.pdf"
        edge_args = [str(EDGE), "--headless=new", "--disable-gpu", f"--print-to-pdf={pdf}", "--no-pdf-header-footer",
                     "--print-to-pdf-no-header", page.as_uri()]
        subprocess.run(edge_args, check=False, capture_output=True, timeout=120)  # noqa: S603 - fixed Edge path, own file
        print(f"{pdf.name}: {pdf.stat().st_size / 1e6:.2f} MB" if pdf.exists() else f"{pdf.name}: NOT CREATED")
    # Q10/Q17/Q18 counts so the form matches the PDF.
    (out_dir / "form-answers.md").write_text(f"""# Values for the Microsoft Form (must match the PDF)

- Q8 Project type: <choose up to 2>
- Q9 Main AI tool and model: qwen3:8b (local, via Ollama) in a Python multi-agent app (Streamlit)
- Q10 Number of prompts: {n_prompts}
- Q11 Prototype link: {L['prototype']}
- Q12 Repository link: {L['repository']}
- Q13 Reel link: {L['reel']}
- Q14 Reel length (seconds): {team['reel_seconds']}
- Q15 Supporting document type: Technical report
- Q16 Upload: {sup_name}.pdf
- Q17 Number of test inputs: {len(rows)}
- Q18 Number of prompt versions: {n_versions}
- Q19 Prototype status: Some modules working ({sum(1 for m in MODULES if m[2] == 'Working')} of {len(MODULES)} Working, rest Partly working)
- Q20 Working modules:
""" + "\n".join(f"  - {m[0]}" for m in MODULES if m[2] == "Working") + f"""
- Q21 Upload: {m2_name}.pdf
- Q22 Main task of each member:
""" + "\n".join(f"  - {m['enrollment']} – {m['name']} – {m['tasks']}" for m in team["members"]) + """
- Q23 Note for evaluator: Open the Streamlit link and choose "Saved test runs" in the sidebar to see every recorded
  test run (no login). "Ask live" works while our model server is online (shown in the sidebar).
""", encoding="utf-8")
    print("wrote", out_dir / "form-answers.md")


if __name__ == "__main__":
    if not EDGE.exists() and not shutil.which("msedge"):
        sys.exit("Microsoft Edge is needed to print the PDFs.")
    build()
