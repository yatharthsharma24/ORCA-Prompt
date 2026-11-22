"""Planner and specialist agent definitions (role prompts + tool sets)."""

from __future__ import annotations

from dataclasses import dataclass

from orca.data.store import MarineStore


@dataclass(frozen=True)
class Specialist:
    key: str
    title: str
    focus: str
    tools: tuple[str, ...]


SPECIALISTS: dict[str, Specialist] = {
    s.key: s
    for s in (
        Specialist(
            "oceanography",
            "Oceanography Analyst",
            "sea surface temperature, chlorophyll-a, salinity, dissolved oxygen, monsoon upwelling, "
            "long-term trends, anomalies, marine heatwaves and coral heat stress",
            ("describe_dataset", "query_data", "run_sql", "trend", "anomaly", "calculate", "search_knowledge"),
        ),
        Specialist(
            "fisheries",
            "Fisheries Analyst",
            "fish landings by state and species, catch trends, and links between landings and ocean "
            "conditions (with lags)",
            ("describe_dataset", "query_data", "run_sql", "trend", "correlate", "calculate", "search_knowledge"),
        ),
        Specialist(
            "biodiversity",
            "Biodiversity Analyst",
            "species occurrence and survey counts, protected species, eDNA detections, coral reef "
            "condition, taxonomy, and live OBIS occurrence records",
            ("describe_dataset", "query_data", "run_sql", "species_lookup", "trend", "correlate",
             "calculate", "obis_occurrences", "search_knowledge"),
        ),
        Specialist(
            "context",
            "Marine Science Context Specialist",
            "definitions, thresholds and mechanisms from curated, cited background notes "
            "(bleaching alert levels, heatwave definitions, upwelling, species biology, data caveats)",
            ("search_knowledge", "species_lookup"),
        ),
    )
}

PLANNER_TOOLS = ("describe_dataset", "search_knowledge")


def data_guide(store: MarineStore) -> str:
    """Compact description of every table, shown to the planner and specialists."""
    lines = []
    for d in store.datasets:
        cols = ", ".join(d.columns)
        tag = " [SYNTHETIC demo data]" if d.synthetic else ""
        lines.append(f"- {d.dataset_id} = table {d.table_name}: {d.title}{tag}\n  columns: {cols}")
        if "year" in d.columns:
            r = store.query(f'SELECT MIN(year) AS lo, MAX(year) AS hi FROM "{d.table_name}"')[0]
            lines.append(f"  years: {r['lo']}-{r['hi']}")
        # List the valid values of low-cardinality text columns so agents filter correctly.
        for col in d.columns:
            if col in ("record_id", "common_name", "habitat", "family", "method"):
                continue
            try:
                vals = store.distinct_values(d.table_name, col, limit=16)
            except Exception:  # noqa: S112 - a column that can't be listed is simply left out of the guide
                continue
            if vals and len(vals) <= 15 and all(isinstance(v, str) for v in vals):
                lines.append(f"  {col} values: {vals}")
    return "\n".join(lines)


def planner_prompt(guide: str, max_steps: int) -> str:
    specs = "\n".join(f"- {s.key}: {s.focus}" for s in SPECIALISTS.values())
    return f"""You are the ORCA Planner, the lead agent of a team that answers questions about marine ecosystems around India.
Turn the user's question into a short plan of 1 to {max_steps} steps. Each step goes to one specialist:
{specs}

Available data:
{guide}

Planning rules:
- Use as few steps as the question needs. Most questions need 1 or 2 steps; never add a step that only repeats another.
- Each task must be concrete: name the dataset id, filters (region/state/species/months/years) and method (trend, correlation with lag, anomaly vs baseline, lookup).
- Add a 'context' step only when a definition or threshold from background notes is needed to interpret results.
- If the question cannot be answered with this data, still return one step explaining what to check.
- The data list above is usually enough. Call a tool only if you truly cannot tell which data applies.

Reply with a Thought, then "Final Answer:" followed by only a JSON object, no prose:
{{"steps": [{{"specialist": "oceanography", "task": "..."}}]}}"""


def specialist_prompt(spec: Specialist, guide: str) -> str:
    return f"""You are the ORCA {spec.title}. You specialise in {spec.focus}.
You are one member of a multi-agent team. Do only your assigned step, using tools to get real numbers from the data.

Available data:
{guide}

Working rules:
- Prefer query_data, trend, correlate and anomaly over raw SQL. Use run_sql for joins.
- Use exact filter values from the data list (e.g. region "SE Arabian Sea", state "Kerala").
- Keep the Final Answer short: 2-5 sentences of findings with [E#] citations and the key numbers."""
