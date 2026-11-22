"""Synthesizer: writes the final cited answer from step findings and evidence."""

from __future__ import annotations

from orca.evidence import EvidenceLedger
from orca.llm import LLM

SYSTEM = """You are the ORCA Synthesizer. You write the final answer to a marine-ecosystem question for scientists and fisheries/conservation managers in India.

You receive the question, the plan, each specialist's findings and the evidence records (E1, E2, ...).

Write in Markdown with this layout:
- First, a direct answer in 1-3 sentences (a plain paragraph, no heading or numbering), with [E#] citations after any number.
- Then "**Key findings**" as 2-6 bullets. End every bullet with its evidence ids in square brackets, e.g. [E2] or [E2, E4].
- Then "**Caveats**" as 1-3 short bullets (data limits, short records, correlation vs causation, synthetic demo data when any cited evidence is synthetic). Do not repeat a point.

Strict rules:
- Copy every number exactly as it appears in the evidence (you may round to fewer decimals). Never compute new numbers yourself.
- When the evidence lists items (months, years, sites, species), report them exactly. Do not merge them into ranges such as "January to December 2017" unless every item in that range appears in the evidence; summarise with counts per year instead if the list is long.
- Cite only evidence ids that exist in the evidence list. Do not cite step names or datasets directly.
- If the evidence does not answer part of the question, say that plainly instead of guessing.
- Do not add a references section; the system appends one."""


def evidence_digest(ledger: EvidenceLedger, limit: int = 900) -> str:
    blocks = []
    for ev in ledger.all():
        src = "; ".join(f"{s.title}{' (SYNTHETIC)' if s.synthetic else ''}" for s in ev.sources) or "n/a"
        text = ev.text if len(ev.text) <= limit else ev.text[:limit] + " ...(truncated)"
        blocks.append(f"[{ev.id}] tool={ev.tool} source={src}\n{text}")
    return "\n\n".join(blocks) if blocks else "(no evidence was collected)"


def build_messages(question: str, plan: list[dict], findings: list[dict], ledger: EvidenceLedger) -> list[dict]:
    plan_txt = "\n".join(f"{i + 1}. [{s['specialist']}] {s['task']}" for i, s in enumerate(plan))
    find_txt = "\n\n".join(
        f"Step {i + 1} ({f['specialist']}): {f['findings'] or '(no findings)'}" for i, f in enumerate(findings)
    )
    user = (
        f"Question: {question}\n\nPlan:\n{plan_txt}\n\nSpecialist findings:\n{find_txt}\n\n"
        f"Evidence:\n{evidence_digest(ledger)}\n\nWrite the final answer now."
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


async def synthesize(llm: LLM, model: str, messages: list[dict]) -> str:
    resp = await llm.chat(messages, model=model)
    return resp.text.strip()


CRITIC_SYSTEM = """You are the ORCA Evidence Critic. You check a draft answer against the evidence records it cites.

Report a claim only if:
- the evidence contradicts it, or
- it attaches a number to the wrong site, region, year, species or variable, or
- it states a threshold, category or fact that the cited evidence does not contain.

A simple comparison of an evidence number with a threshold stated in the evidence (e.g. 12.1 is above 12) counts as supported, as do rounding and paraphrase.
Standard proxies count as supported when the question or findings use them (e.g. monsoon chlorophyll-a as an indicator of upwelling strength); do not report a claim only because the evidence uses the proxy's name.
Do not report style, tone or missing detail. Be strict but do not invent problems. If every claim is supported, return an empty list."""

CRITIC_SCHEMA = {
    "type": "object",
    "properties": {
        "problems": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"claim": {"type": "string"}, "problem": {"type": "string"}},
                "required": ["claim", "problem"],
            },
        }
    },
    "required": ["problems"],
}


async def critique(llm: LLM, model: str, question: str, answer: str, ledger: EvidenceLedger) -> list[dict[str, str]]:
    """LLM check of claim-level support; complements the deterministic number check."""
    import json

    messages = [
        {"role": "system", "content": CRITIC_SYSTEM},
        {"role": "user", "content": f"Question: {question}\n\nEvidence:\n{evidence_digest(ledger, limit=1500)}"
                                    f"\n\nDraft answer:\n{answer}\n\nList the unsupported claims as JSON."},
    ]
    resp = await llm.chat(messages, model=model, json_schema=CRITIC_SCHEMA)
    try:
        problems = json.loads(resp.text).get("problems", [])
    except (ValueError, AttributeError):
        return []
    return [
        {"claim": str(p.get("claim", ""))[:400], "problem": str(p.get("problem", ""))[:400]}
        for p in problems if isinstance(p, dict) and p.get("problem")
    ][:6]


def revision_messages(messages: list[dict], draft: str, issues: list[str]) -> list[dict]:
    issue_txt = "\n".join(f"- {i}" for i in issues)
    return messages + [
        {"role": "assistant", "content": draft},
        {
            "role": "user",
            "content": "The verifier checked your answer against the evidence and found problems:\n"
            f"{issue_txt}\n\nRewrite the whole answer. For each problem either use the exact number from the "
            "evidence with the correct [E#] citation, or remove the claim. Keep the same structure.",
        },
    ]
