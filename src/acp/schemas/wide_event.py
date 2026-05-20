"""WideEvent — flat row matching the SQLite `wide_events` table.

This is the storage primitive: SLI is a query over these rows.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


EventType = Literal[
    "task_start",
    "tool_call",
    "tool_result",
    "task_end",
    "judgment",
    "intervention",
    "outcome",
    "autonomy_change",
]
Outcome = Literal["ok", "denied", "error", "escalated", "pending"]
TierStr = Literal["T0", "T1", "T2", "T3", "T4"]


class WideEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(..., min_length=1)
    prev_event_id: str | None = None
    ts: int = Field(..., ge=0)
    run_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    task_class: str = Field(..., min_length=1)
    model_version: str = Field(..., min_length=1)
    step: int = Field(..., ge=0)
    event_type: EventType
    tool_name: str | None = None
    tier_required: TierStr | None = None
    outcome: Outcome | None = None
    intent: str | None = None
    agent_claim: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)
    chain_hash: str = Field(..., min_length=1)


def to_db_row(event: WideEvent) -> dict[str, Any]:
    """Serialize a WideEvent to a dict matching the wide_events table columns.

    `attrs` is JSON-encoded into `attrs_json`.
    """
    return {
        "event_id": event.event_id,
        "prev_event_id": event.prev_event_id,
        "ts": event.ts,
        "run_id": event.run_id,
        "agent_id": event.agent_id,
        "task_class": event.task_class,
        "model_version": event.model_version,
        "step": event.step,
        "event_type": event.event_type,
        "tool_name": event.tool_name,
        "tier_required": event.tier_required,
        "outcome": event.outcome,
        "intent": event.intent,
        "agent_claim": event.agent_claim,
        "attrs_json": json.dumps(event.attrs, sort_keys=True, separators=(",", ":")),
        "chain_hash": event.chain_hash,
    }


def from_db_row(row: dict[str, Any]) -> WideEvent:
    """Inverse of `to_db_row`. Accepts either a dict or sqlite3.Row-like mapping."""
    raw = row.get("attrs_json")
    if raw is None:
        attrs: dict[str, Any] = {}
    elif isinstance(raw, str):
        attrs = json.loads(raw) if raw else {}
    else:
        attrs = dict(raw)
    return WideEvent(
        event_id=row["event_id"],
        prev_event_id=row.get("prev_event_id"),
        ts=row["ts"],
        run_id=row["run_id"],
        agent_id=row["agent_id"],
        task_class=row["task_class"],
        model_version=row["model_version"],
        step=row["step"],
        event_type=row["event_type"],
        tool_name=row.get("tool_name"),
        tier_required=row.get("tier_required"),
        outcome=row.get("outcome"),
        intent=row.get("intent"),
        agent_claim=row.get("agent_claim"),
        attrs=attrs,
        chain_hash=row["chain_hash"],
    )
