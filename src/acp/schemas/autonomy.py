"""AutonomyTierChange — emitted by the autonomy controller."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .agent import AutonomyTier


class AutonomyTierChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(..., min_length=1)
    task_class: str = Field(..., min_length=1)
    old_tier: AutonomyTier
    new_tier: AutonomyTier
    cause: str = Field(..., min_length=1)
    burn_rate: float | None = None
    boundary_delta: float | None = None
    ts: int = Field(..., ge=0)
