"""Knowledge-note search and live OBIS occurrence queries."""

from __future__ import annotations

from typing import Any

import httpx

from orca.evidence import Source, tidy
from orca.tools.base import Tool, ToolContext, ToolError, ToolResult

# Rough bounding boxes (lon/lat) for the regions used in the demo tables.
REGION_BOXES = {
    "SE Arabian Sea": (73.0, 7.5, 77.5, 14.5),
    "NE Arabian Sea": (66.0, 15.0, 73.5, 24.0),
    "SW Bay of Bengal": (78.0, 8.0, 83.0, 16.0),
    "NW Bay of Bengal": (83.0, 16.0, 90.0, 22.5),
    "Andaman Sea": (91.5, 6.0, 95.5, 14.5),
    "Lakshadweep Sea": (71.0, 8.0, 74.5, 12.5),
    "India EEZ": (65.0, 4.0, 97.0, 24.5),
}


def _wkt(box: tuple[float, float, float, float]) -> str:
    x0, y0, x1, y1 = box
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


class SearchKnowledge(Tool):
    name = "search_knowledge"
    description = (
        "Search curated background notes (coral bleaching thresholds, marine heatwave definitions, monsoon "
        "upwelling, oil sardine, hilsa, protected species, eDNA caveats, IPCC findings). Returns cited passages."
    )
    example = {"query": "degree heating weeks bleaching threshold", "k": 3}

    async def run(self, args, ctx: ToolContext) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("Missing 'query'.")
        k = max(1, min(int(args.get("k", 3)), 5))
        hits = ctx.kb.search(query, k)
        if not hits:
            return ToolResult(f"No notes match '{query}'.", record=False)
        passages = [{"id": p.passage_id, "title": p.title, "text": p.text} for p, _ in hits]
        seen: dict[str, Source] = {}
        for p, _ in hits:
            seen.setdefault(p.doc_id, Source("knowledge", p.doc_id, p.title, p.source, p.url))
        text = "\n\n".join(f"[{p['id']}] {p['title']}: {p['text']}" for p in passages)
        # Knowledge passages are text, not measurements: keep numbers out of the data payload
        # so the verifier treats them as citable text via the passage strings.
        return ToolResult(text, data={"query": query, "passages": passages}, sources=list(seen.values()))


class ObisOccurrences(Tool):
    name = "obis_occurrences"
    description = (
        "Query the live Ocean Biodiversity Information System (OBIS, real data) for occurrence records of a "
        "taxon in a region: total records, records per year, and the contributing datasets with citations. "
        f"region is one of {list(REGION_BOXES)}."
    )
    example = {"scientific_name": "Rhincodon typus", "region": "NE Arabian Sea", "start_year": 2000, "end_year": 2024}

    async def run(self, args, ctx: ToolContext) -> ToolResult:
        if not ctx.settings.enable_obis:
            raise ToolError("The OBIS tool is disabled on this server (ORCA_ENABLE_OBIS=false).")
        name = str(args.get("scientific_name") or args.get("species") or "").strip()
        if not name:
            raise ToolError("Missing 'scientific_name'.")
        region = str(args.get("region") or "India EEZ")
        if region not in REGION_BOXES:
            raise ToolError(f"Unknown region '{region}'. Use one of {list(REGION_BOXES)}.")
        params: dict[str, Any] = {"scientificname": name, "geometry": _wkt(REGION_BOXES[region])}
        if args.get("start_year"):
            params["startdate"] = f"{int(args['start_year'])}-01-01"
        if args.get("end_year"):
            params["enddate"] = f"{int(args['end_year'])}-12-31"
        base = ctx.settings.obis_base_url.rstrip("/")
        client = ctx.http or httpx.AsyncClient(timeout=ctx.settings.obis_timeout_s)
        try:
            occ = await client.get(f"{base}/occurrence", params={**params, "size": 1})
            occ.raise_for_status()
            total = occ.json().get("total", 0)
            years: list[dict[str, Any]] = []
            datasets: list[dict[str, Any]] = []
            if total:
                yr = await client.get(f"{base}/statistics/years", params=params)
                yr.raise_for_status()
                years = yr.json() if isinstance(yr.json(), list) else []
                dsr = await client.get(f"{base}/dataset", params=params)
                dsr.raise_for_status()
                datasets = sorted(dsr.json().get("results", []), key=lambda d: -(d.get("records") or 0))[:5]
        except httpx.HTTPError as exc:
            raise ToolError(f"OBIS request failed: {exc}. Continue with local data.") from exc
        finally:
            if ctx.http is None:
                await client.aclose()

        query_url = str(httpx.URL(f"{base}/occurrence", params=params))
        data = tidy({
            "scientific_name": name, "region": region, "total_records": total,
            "records_by_year": {int(y["year"]): int(y["records"]) for y in years if "year" in y},
            "datasets": [{"title": d.get("title"), "records": d.get("records"), "url": d.get("url")} for d in datasets],
            "query_url": query_url,
        })
        sources = [Source("obis", "OBIS", "Ocean Biodiversity Information System",
                          "OBIS (2026). Ocean Biodiversity Information System. Intergovernmental Oceanographic "
                          "Commission of UNESCO. www.obis.org", query_url)]
        for d in datasets:
            sources.append(Source("obis", f"OBIS:{d.get('id')}", d.get("title") or "OBIS dataset",
                                  d.get("citation") or d.get("title") or "OBIS dataset", d.get("url")))
        lines = [f"OBIS (live, real records): {name} in {region}"
                 + (f", {args.get('start_year')}-{args.get('end_year')}" if args.get("start_year") else ""),
                 f"Total occurrence records: {total}"]
        if data["records_by_year"]:
            lines.append(f"Records by year: {data['records_by_year']}")
        for d in data["datasets"]:
            lines.append(f"Dataset: {d['title']} ({d['records']} records)")
        lines.append("Record counts reflect sampling effort, not abundance.")
        return ToolResult("\n".join(lines), data=data, sources=sources)
