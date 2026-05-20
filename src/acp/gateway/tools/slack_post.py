"""Mock Slack post — T2 reversible (can delete, but message was seen)."""

from __future__ import annotations

from typing import Any

from acp.gateway.tools.base import sealed_tool
from acp.schemas.tool import ToolSpec

_SPEC = ToolSpec(
    name="slack_post",
    description="Post a message to a Slack channel.",
    input_schema={
        "type": "object",
        "properties": {"channel": {"type": "string"}, "text": {"type": "string"}},
        "required": ["channel", "text"],
    },
    output_schema={"type": "object", "properties": {"ts": {"type": "string"}}},
    tier="T2",
    reversibility="reversible",
    blast_radius="tenant",
    requires_intent=False,
)


@sealed_tool(_SPEC)
def handle(args: dict[str, Any], run_id: str) -> dict[str, Any]:
    return {
        "channel": args.get("channel", "#sre"),
        "ts": "1700000000.000100",
        "ok": True,
    }
