"""Human-in-loop schemas: ApprovalRequest, AuditFinding."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ApprovalStatus = Literal["pending", "approved", "rejected"]
AuditReason = Literal["sample", "escalation", "disagreement", "goodhart_flag"]
AuditStatus = Literal["pending", "reviewed", "dismissed"]


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str = Field(..., min_length=1)
    event_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    intent: str = Field(..., min_length=1)
    args_json: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus = "pending"
    decided_by: str | None = None
    decided_at: int | None = None


class AuditFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str = Field(..., min_length=1)
    event_id: str = Field(..., min_length=1)
    reason: AuditReason
    status: AuditStatus = "pending"
    reviewer: str | None = None
    notes: str = ""
    human_label: str | None = None
