"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let evidenceIds = new Set();
let lastResult = null;
let stepEls = {};

function store(key, value) {
  try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch { return null; }
}

// ---------- markdown with citation chips ----------
function inline(text) {
  let s = esc(text);
  s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/(^|\W)_(.+?)_(?=\W|$)/g, "$1<em>$2</em>");
  s = s.replace(/\[(E\d+(?:\s*[,;]\s*E\d+)*)\]/g, (_, grp) =>
    grp.split(/[,;]/).map((id) => {
      id = id.trim();
      const bad = evidenceIds.size && !evidenceIds.has(id);
      return `<button type="button" class="cite${bad ? " bad" : ""}" data-ev="${id}" title="${bad ? "unknown evidence" : "show evidence"}">${id}</button>`;
    }).join(" "));
  return s;
}

function markdown(md) {
  const out = [];
  let list = null;
  for (const raw of String(md || "").split("\n")) {
    const line = raw.trim();
    const bullet = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (bullet) {
      if (!list) { list = []; }
      list.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; }
    if (!line) continue;
    const h = line.match(/^#{1,4}\s+(.*)$/);
    out.push(h ? `<h3>${inline(h[1])}</h3>` : `<p>${inline(line)}</p>`);
  }
  if (list) out.push(`<ul>${list.join("")}</ul>`);
  return out.join("");
}

// ---------- rendering ----------
function setStatus(kind, text) {
  $("status").className = `status ${kind}`;
  $("status-text").textContent = text;
}

function resetRun() {
  evidenceIds = new Set();
  stepEls = {};
  lastResult = null;
  $("plan").innerHTML = ""; $("trace").innerHTML = ""; $("evidence").innerHTML = "";
  $("answer").innerHTML = ""; $("references").innerHTML = ""; $("verify-body").innerHTML = "";
  $("verify-badge").textContent = ""; $("verify-badge").className = "badge"; $("phase").textContent = "";
  $("synthetic-note").hidden = true; $("answer-panel").hidden = true;
  $("evidence-empty").hidden = false;
  $("plan-panel").hidden = false; $("trace-panel").hidden = false;
}

function renderPlan(steps, source) {
  $("plan-source").textContent = source ? `(${source})` : "";
  $("plan").innerHTML = steps.map((s, i) => `<li id="plan-S${i + 1}"><span class="spec">${esc(s.specialist)}</span>${esc(s.task)}</li>`).join("");
}

function stepBox(stepId, title, task) {
  const el = document.createElement("div");
  el.className = "step";
  el.innerHTML = `<div class="step-title">${esc(stepId)} &middot; ${esc(title)}</div><div class="step-task">${esc(task)}</div>`;
  $("trace").appendChild(el);
  stepEls[stepId] = el;
  return el;
}

function currentStep() {
  const keys = Object.keys(stepEls);
  if (!keys.length) return stepBox("Planner", "Planner", "Turning the question into a step plan");
  return stepEls[keys[keys.length - 1]];
}

function addTrace(cls, html) {
  const div = document.createElement("div");
  div.className = `t ${cls}`;
  div.innerHTML = html;
  currentStep().appendChild(div);
  div.scrollIntoView({ block: "nearest" });
}

function addEvidence(id, tool, text, sources) {
  if (!id || evidenceIds.has(id)) return;
  evidenceIds.add(id);
  $("evidence-empty").hidden = true;
  const synthetic = (sources || []).some((s) => s.synthetic);
  const div = document.createElement("div");
  div.className = "ev"; div.id = `ev-${id}`;
  div.innerHTML = `<div class="ev-head"><span>${esc(id)} &middot; ${esc(tool)}</span>${synthetic ? '<span class="synthetic-tag">SYNTHETIC</span>' : ""}</div><pre>${esc(text)}</pre>`;
  $("evidence").appendChild(div);
}

function badge(v) {
  const critic = (v.critic_problems || []).length;
  const el = $("verify-badge");
  if (v.passed && !critic) { el.className = "badge pass"; el.textContent = `verified ${v.verified_claims}/${v.numeric_claims} numeric claims`; }
  else if (v.passed) { el.className = "badge warn"; el.textContent = `numbers verified, ${critic} critic note${critic > 1 ? "s" : ""}`; }
  else { el.className = "badge fail"; el.textContent = `unverified claims (${v.verified_claims}/${v.numeric_claims})`; }
}

function renderVerification(v) {
  badge(v);
  const claims = (v.claims || []).filter((c) => c.status !== "no_numbers");
  let html = `<p class="muted">Every number in the answer is matched against the evidence it cites. Score ${v.score} (${v.verified_claims}/${v.numeric_claims}).</p>`;
  html += claims.map((c) => `<div class="claim ${esc(c.status)}"><code>${esc(c.status)}</code> ${inline(c.text)}</div>`).join("");
  if ((v.issues || []).length) html += `<h3>Issues</h3><ul>${v.issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`;
  if ((v.critic_problems || []).length) html += `<h3>Critic agent notes</h3><ul>${v.critic_problems.map((p) => `<li>${esc(p.problem)} <em>(${esc(p.claim)})</em></li>`).join("")}</ul>`;
  $("verify-body").innerHTML = html;
}

function renderFinal(r) {
  lastResult = r;
  for (const ev of r.evidence || []) addEvidence(ev.id, ev.tool, ev.text, ev.sources);
  $("answer-panel").hidden = false;
  $("answer").innerHTML = markdown(r.answer);
  $("synthetic-note").hidden = !r.uses_synthetic_data;
  renderVerification(r.verification);
  $("references").innerHTML = (r.references || []).map((ref) => {
    const srcs = ref.sources.map((s) => {
      const link = s.url ? ` <a href="${esc(s.url)}" target="_blank" rel="noopener">link</a>` : "";
      return `${esc(s.citation)}${s.synthetic ? '<span class="synthetic-tag">SYNTHETIC</span>' : ""}${link}`;
    }).join("<br>");
    return `<li id="ref-${esc(ref.id)}"><strong>${esc(ref.id)}</strong> <span class="tool">${esc(ref.tool)} ${esc(JSON.stringify(ref.args))}</span><br>${srcs || "computed"}</li>`;
  }).join("");
  $("run-link").href = `/api/runs/${encodeURIComponent(r.run_id)}`;
  $("phase").textContent = `done in ${r.elapsed_s}s${r.revisions ? `, ${r.revisions} revision` : ""}`;
  document.querySelectorAll("#plan li").forEach((li) => li.classList.add("done"));
  history.replaceState(null, "", `#run=${r.run_id}`);
}

function renderStoredTrace(r) {
  renderPlan(r.plan, r.plan_source);
  for (const s of r.steps || []) {
    stepBox(s.step_id, s.title, s.task);
    for (const t of s.trace || []) {
      if (t.thought) addTrace("", `<b>Thought:</b> ${esc(t.thought)}`);
      if (t.action) addTrace("", `<b>Action:</b> ${esc(t.action)} ${esc(JSON.stringify(t.action_input || {}))}`);
      if (t.observation) addTrace(`obs${t.error ? " err" : ""}`, `<b>Observation:</b> ${esc(t.observation)}`);
    }
    addTrace("find", `<b>Findings:</b> ${inline(s.findings)}`);
  }
}

// ---------- streaming ----------
function handle(kind, p) {
  switch (kind) {
    case "accepted": $("phase").textContent = `run ${p.run_id}`; break;
    case "queued": $("phase").textContent = `waiting for the GPU (position ${p.position})`; break;
    case "phase": $("phase").textContent = p.phase; break;
    case "plan": renderPlan(p.steps, p.source); break;
    case "step_started":
      document.querySelectorAll("#plan li").forEach((li) => li.classList.remove("active"));
      $(`plan-${p.step_id}`)?.classList.add("active");
      stepBox(p.step_id, p.title, p.task); break;
    case "thought": if (p.text) addTrace("", `<b>Thought:</b> ${esc(p.text)}`); break;
    case "action": addTrace("", `<b>Action:</b> ${esc(p.tool)} ${esc(typeof p.input === "string" ? p.input : JSON.stringify(p.input))}`); break;
    case "observation":
      addTrace(`obs${p.error ? " err" : ""}`, `<b>Observation:</b> ${esc(p.text)}`);
      if (p.evidence_id) addEvidence(p.evidence_id, p.tool, p.text.replace(/^\[E\d+\]\s*/, ""), p.text.includes("SYNTHETIC") ? [{ synthetic: true }] : []);
      break;
    case "step_finished":
      $(`plan-${p.step_id}`)?.classList.replace("active", "done");
      addTrace("find", `<b>Findings:</b> ${inline(p.findings)}`); break;
    case "draft":
      $("answer-panel").hidden = false;
      $("answer").innerHTML = markdown(p.answer); break;
    case "verification": renderVerification(p); break;
    case "final": renderFinal(p); loadHistory(); break;
    case "error": $("phase").textContent = `error: ${p.message}`; setStatus("bad", "run failed"); break;
  }
}

async function ask(question) {
  resetRun();
  $("ask-btn").disabled = true;
  const headers = { "Content-Type": "application/json" };
  const key = $("api-key").value.trim();
  if (key) { headers["X-API-Key"] = key; store("orca-key", key); }
  try {
    const resp = await fetch("/api/ask", { method: "POST", headers, body: JSON.stringify({ question }) });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        let kind = "message", data = "";
        for (const line of chunk.split("\n")) {
          if (line.startsWith("event:")) kind = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (data) handle(kind, JSON.parse(data));
      }
    }
  } catch (err) {
    $("phase").textContent = `error: ${err.message}`;
  } finally {
    $("ask-btn").disabled = false;
  }
}

// ---------- side panels ----------
async function loadHistory() {
  const runs = await fetch("/api/runs?limit=12").then((r) => r.json()).catch(() => []);
  $("history").innerHTML = runs.map((r) => `<li><a href="#run=${esc(r.id)}" data-run="${esc(r.id)}">${esc(r.question)}</a>
    <div class="meta">${esc(r.status)}${r.elapsed_s ? ` &middot; ${r.elapsed_s}s` : ""}${r.score != null ? ` &middot; score ${r.score}` : ""}</div></li>`).join("") || '<li class="muted">No runs yet.</li>';
}

async function openRun(id) {
  const run = await fetch(`/api/runs/${encodeURIComponent(id)}`).then((r) => (r.ok ? r.json() : null));
  if (!run || !run.result) return;
  resetRun();
  $("question").value = run.question;
  renderStoredTrace(run.result);
  renderFinal(run.result);
}

async function init() {
  const cfg = await fetch("/api/config").then((r) => r.json()).catch(() => null);
  if (cfg) {
    $("examples").innerHTML = cfg.examples.map((q) => `<button type="button">${esc(q)}</button>`).join("");
    if (cfg.notice) { $("notice").textContent = cfg.notice; $("notice").hidden = false; }
    if (cfg.auth_required) { $("api-key").hidden = false; $("api-key").value = store("orca-key") || ""; }
  }
  fetch("/api/ready").then(async (r) => {
    const body = await r.json();
    if (body.ready) setStatus("ok", `${body.llm.model} ready`);
    else setStatus("bad", body.llm.reachable ? `model ${body.llm.model} not pulled` : "Ollama unreachable");
  }).catch(() => setStatus("bad", "server unreachable"));
  fetch("/api/datasets").then((r) => r.json()).then((ds) => {
    $("datasets").innerHTML = ds.map((d) => `<li><strong>${esc(d.dataset_id)}</strong> ${esc(d.title)}${d.synthetic ? '<span class="synthetic-tag">SYNTHETIC</span>' : ""}</li>`).join("");
  }).catch(() => {});
  loadHistory();
  const m = location.hash.match(/run=([\w-]+)/);
  if (m) openRun(m[1]);
}

document.addEventListener("click", (e) => {
  const cite = e.target.closest(".cite");
  if (cite) {
    const el = $(`ev-${cite.dataset.ev}`);
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1400); }
    return;
  }
  const ex = e.target.closest(".examples button");
  if (ex) { $("question").value = ex.textContent; $("question").focus(); return; }
  const run = e.target.closest("[data-run]");
  if (run) { e.preventDefault(); openRun(run.dataset.run); }
});
$("ask-form").addEventListener("submit", (e) => { e.preventDefault(); const q = $("question").value.trim(); if (q) ask(q); });
$("question").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("ask-form").requestSubmit(); });
$("copy-md").addEventListener("click", () => { if (lastResult) navigator.clipboard.writeText(lastResult.answer_markdown).catch(() => {}); });
init();
