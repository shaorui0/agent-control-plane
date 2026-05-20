"""Mock runbook search — T1 read-only."""

from __future__ import annotations

from typing import Any

from acp.gateway.tools.base import sealed_tool
from acp.schemas.tool import ToolSpec

_SPEC = ToolSpec(
    name="runbook_search",
    description="Search runbook corpus.",
    input_schema={
        "type": "object",
        "properties": {"q": {"type": "string"}, "k": {"type": "integer"}},
        "required": ["q"],
    },
    output_schema={"type": "object", "properties": {"hits": {"type": "array"}}},
    tier="T1",
    reversibility="read",
    blast_radius="self",
    requires_intent=False,
)


@sealed_tool(_SPEC)
def handle(args: dict[str, Any], run_id: str) -> dict[str, Any]:
    q = args.get("q", "")
    return {
        "hits": [
            {"title": "How to triage CPU spikes", "score": 0.92, "q_echo": q},
        ]
    }
