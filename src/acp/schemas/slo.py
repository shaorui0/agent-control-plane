"""SLO schemas: SLODefinition, BurnRateWindow, BudgetSnapshot."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .agent import BudgetClass, SliKind


WindowLabel = Literal["1h", "6h", "24h", "7d"]


class SLODefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(..., min_length=1)
    task_class: str = Field(..., min_length=1)
    model_version: str = Field(..., min_length=1)
    sli_kind: SliKind
    target: float = Field(..., ge=0.0)
    window_seconds: int = Field(..., gt=0)
    budget_class: BudgetClass = "organic"


class BurnRateWindow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: WindowLabel
    window_seconds: int = Field(..., gt=0)
    sli_value: float = Field(..., ge=0.0)
    target: float = Field(..., ge=0.0)
    burn_rate: float = Field(..., ge=0.0)


class BudgetSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str = Field(..., min_length=1)
    ts: int = Field(..., ge=0)
    agent_id: str = Field(..., min_length=1)
    task_class: str = Field(..., min_length=1)
    model_version: str = Field(..., min_length=1)
    window_label: WindowLabel
    budget_class: BudgetClass
    sli_value: float = Field(..., ge=0.0)
    slo_target: float = Field(..., ge=0.0)
    burn_rate: float = Field(..., ge=0.0)
    budget_remaining: float
