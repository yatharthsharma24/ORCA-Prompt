"""Tool registry."""

from __future__ import annotations

from orca.tools.analysis import Anomaly, Calculate, Correlate, Trend
from orca.tools.base import Tool, ToolContext, ToolError, ToolResult
from orca.tools.data import DescribeDataset, ListDatasets, QueryData, RunSQL, SpeciesLookup
from orca.tools.external import ObisOccurrences, SearchKnowledge

ALL_TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        ListDatasets(), DescribeDataset(), QueryData(), RunSQL(), SpeciesLookup(),
        Trend(), Correlate(), Anomaly(), Calculate(), SearchKnowledge(), ObisOccurrences(),
    )
}

__all__ = ["ALL_TOOLS", "Tool", "ToolContext", "ToolError", "ToolResult"]
