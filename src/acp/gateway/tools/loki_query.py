"""Mock Loki LogQL query — T1 read-only."""

from __future__ import annotations

from typing import Any

from acp.gateway.tools.base import sealed_tool
from acp.schemas.tool import ToolSpec

_SPEC = ToolSpec(
    name="loki_query",
    description="Run a LogQL query against Loki (read-only).",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}},
        "required": ["query"],
    },
    output_schema={"type": "object", "properties": {"streams": {"type": "array"}}},
    tier="T1",
    reversibility="read",
    blast_radius="self",
    requires_intent=False,
)


@sealed_tool(_SPEC)
def handle(args: dict[str, Any], run_id: str) -> dict[str, Any]:
    return {"streams": [{"labels": {"app": "mock"}, "values": []}], "query_echo": args.get("query", "")}
