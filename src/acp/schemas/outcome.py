"""OutcomeSignal — externally-derived ground truth (trust=external)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


OutcomeKind = Literal[
    "git_applied",
    "oncall_refire",
    "csat_proxy",
    "cost_delta",
    "rollback_required",
]


class OutcomeSignal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    kind: OutcomeKind
    # Polymorphic payload — serialize whatever the source emits.
    value_json: dict[str, Any] = Field(default_factory=dict)
    delay_seconds: int = Field(..., ge=0)
    source: str = Field(..., min_length=1)
    ts: int = Field(..., ge=0)
