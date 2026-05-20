"""Mock VictoriaMetrics PromQL query — T1 read-only."""

from __future__ import annotations

from typing import Any

from acp.gateway.tools.base import sealed_tool
from acp.schemas.tool import ToolSpec

_SPEC = ToolSpec(
    name="vm_query",
    description="Run a PromQL instant query against VictoriaMetrics (read-only).",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}, "time": {"type": "string"}},
        "required": ["query"],
    },
    output_schema={"type": "object", "properties": {"series": {"type": "array"}}},
    tier="T1",
    reversibility="read",
    blast_radius="self",
    requires_intent=False,
)


@sealed_tool(_SPEC)
def handle(args: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Return a deterministic mock series. Real impl would call VM HTTP API."""
    q = args.get("query", "")
    return {
        "series": [{"metric": {"__name__": "mock"}, "value": [0, "1.0"]}],
        "query_echo": q,
        "run_id": run_id,
    }
