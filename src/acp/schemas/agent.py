"""AgentSpec + AutonomyTier + TaskClassConfig + ToolBinding."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class AutonomyTier(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"


SliKind = Literal[
    "judge_pass_rate",
    "intervention_free_rate",
    "silent_fail_rate",
    "p95_latency_ms",
]
BudgetClass = Literal["organic", "adversarial"]


class TaskClassConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    slo_sli_kind: SliKind
    slo_target: float = Field(..., ge=0.0)
    slo_window: str = Field(..., min_length=2)
    slo_budget_class: BudgetClass = "organic"


class ToolBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    max_tier: AutonomyTier
    requires_intent: bool = True
    kwargs_constraints: dict[str, Any] = Field(default_factory=dict)


class AgentSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(..., min_length=1)
    owner: str = Field(..., min_length=3)
    version: str = Field(..., min_length=1)
    model_version: str = Field(..., min_length=1)
    description: str = ""
    task_classes: list[TaskClassConfig] = Field(..., min_length=1)
    sealed_tools: list[ToolBinding] = Field(..., min_length=1)
    budget_hourly_usd: float = Field(..., ge=0.0)
    budget_hourly_tokens: int = Field(..., ge=0)
    default_tier: AutonomyTier = AutonomyTier.T1

    @field_validator("owner")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("owner must be an email address")
        return v
