"""Mock kubectl scale — T3 requires approval, reversible-ish."""

from __future__ import annotations

from typing import Any

from acp.gateway.tools.base import sealed_tool
from acp.schemas.tool import ToolSpec

_SPEC = ToolSpec(
    name="kubectl_scale",
    description="Scale a deployment by N replicas.",
    input_schema={
        "type": "object",
        "properties": {
            "deployment": {"type": "string"},
            "namespace": {"type": "string"},
            "replicas_delta": {"type": "integer"},
        },
        "required": ["deployment", "replicas_delta"],
    },
    output_schema={"type": "object", "properties": {"new_replicas": {"type": "integer"}}},
    tier="T3",
    reversibility="reversible",
    blast_radius="tenant",
    requires_intent=True,
)


@sealed_tool(_SPEC)
def handle(args: dict[str, Any], run_id: str) -> dict[str, Any]:
    delta = int(args.get("replicas_delta", 0))
    return {
        "deployment": args.get("deployment", "unknown"),
        "namespace": args.get("namespace", "default"),
        "applied_delta": delta,
        "new_replicas": 3 + delta,
    }
