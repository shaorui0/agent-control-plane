"""Mock kubectl rollout — T3 requires approval."""

from __future__ import annotations

from typing import Any

from acp.gateway.tools.base import sealed_tool
from acp.schemas.tool import ToolSpec

_SPEC = ToolSpec(
    name="kubectl_rollout",
    description="Rollout restart / status for a deployment.",
    input_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["restart", "status", "undo"]},
            "deployment": {"type": "string"},
            "namespace": {"type": "string"},
        },
        "required": ["action", "deployment"],
    },
    output_schema={"type": "object"},
    tier="T3",
    reversibility="reversible",
    blast_radius="tenant",
    requires_intent=True,
)


@sealed_tool(_SPEC)
def handle(args: dict[str, Any], run_id: str) -> dict[str, Any]:
    return {
        "action": args.get("action", "status"),
        "deployment": args.get("deployment", "unknown"),
        "namespace": args.get("namespace", "default"),
        "result": "ok",
    }
