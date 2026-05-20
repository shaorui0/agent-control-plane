"""Mock kubectl describe — T1 read-only."""

from __future__ import annotations

from typing import Any

from acp.gateway.tools.base import sealed_tool
from acp.schemas.tool import ToolSpec

_SPEC = ToolSpec(
    name="kubectl_describe",
    description="kubectl describe <resource> <name>.",
    input_schema={
        "type": "object",
        "properties": {
            "resource": {"type": "string"},
            "name": {"type": "string"},
            "namespace": {"type": "string"},
        },
        "required": ["resource", "name"],
    },
    output_schema={"type": "object"},
    tier="T1",
    reversibility="read",
    blast_radius="self",
    requires_intent=False,
)


@sealed_tool(_SPEC)
def handle(args: dict[str, Any], run_id: str) -> dict[str, Any]:
    return {
        "kind": args.get("resource", "Pod"),
        "name": args.get("name", "mock-0"),
        "namespace": args.get("namespace", "default"),
        "events": [{"reason": "Pulled", "message": "Container image pulled"}],
    }
