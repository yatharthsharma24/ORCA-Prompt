"""Tool interface shared by every agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

    from orca.config import Settings
    from orca.data.store import MarineStore
    from orca.evidence import EvidenceLedger, Source
    from orca.knowledge import KnowledgeBase


class ToolError(ValueError):
    """Raised for bad tool input; the message is shown to the agent so it can retry."""


@dataclass
class ToolContext:
    store: MarineStore
    ledger: EvidenceLedger
    kb: KnowledgeBase
    settings: Settings
    http: httpx.AsyncClient | None = None
    step_id: str | None = None


@dataclass
class ToolResult:
    text: str
    data: Any = None
    sources: list[Source] = field(default_factory=list)
    record: bool = True  # False for catalog/help output that carries no citable data


class Tool:
    name: str = ""
    description: str = ""
    example: dict[str, Any] = {}

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:  # pragma: no cover
        raise NotImplementedError

    def prompt_line(self) -> str:
        import json

        return f"- {self.name}: {self.description}\n  Example input: {json.dumps(self.example)}"
