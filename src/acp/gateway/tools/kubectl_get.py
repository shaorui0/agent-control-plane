"""Mock kubectl get — T1 read-only."""

from __future__ import annotations

from typing import Any

from acp.gateway.tools.base import sealed_tool
from acp.schemas.tool import ToolSpec

_SPEC = ToolSpec(
    name="kubectl_get",
    description="kubectl get <resource> in <namespace>.",
    input_schema={
        "type": "object",
        "properties": {"resource": {"type": "string"}, "namespace": {"type": "string"}},
        "required": ["resource"],
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
        "items": [
            {
                "kind": args.get("resource", "Pod"),
                "metadata": {"name": "mock-0", "namespace": args.get("namespace", "default")},
                "status": {"phase": "Running"},
            }
        ]
    }
