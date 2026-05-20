"""BaseEvent — common header shared by every wide event.

All concrete event types should subclass this and pin `event_type` to a Literal.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SchemaVersion = Literal["1.0"]


class BaseEvent(BaseModel):
    """Server-stamped event header. Frozen, extra fields forbidden."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(..., min_length=1)
    prev_event_id: str | None = None
    schema_version: SchemaVersion = "1.0"
    ts: int = Field(..., ge=0)
    run_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    task_class: str = Field(..., min_length=1)
    model_version: str = Field(..., min_length=1)
    step: int = Field(..., ge=0)
    event_type: str = Field(..., min_length=1)

    @field_validator("event_id", "run_id", "agent_id", "task_class", "model_version", "event_type")
    @classmethod
    def _no_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("non-empty string required")
        return v
