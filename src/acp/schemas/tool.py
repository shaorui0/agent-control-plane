"""Tool-related schemas: ToolSpec, IntentProof, ToolCallRequest, ToolCallResult."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


TierLiteral = Literal["T0", "T1", "T2", "T3", "T4"]
Reversibility = Literal["read", "reversible", "external", "irreversible"]
BlastRadius = Literal["self", "tenant", "global"]


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    description: str = ""
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    tier: TierLiteral
    reversibility: Reversibility
    blast_radius: BlastRadius
    requires_intent: bool = True


class IntentProof(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(..., min_length=10)
    validated_by_llm: bool = False
    validator_model: str = ""
    score: float = Field(default=1.0, ge=0.0, le=1.0)


class ToolCallRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: str = Field(..., min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    intent: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    idempotency_key: str = Field(..., min_length=26, max_length=26)

    @field_validator("idempotency_key")
    @classmethod
    def _ulid_shape(cls, v: str) -> str:
        # ULID = 26 chars Crockford base32. We validate length+alphabet here.
        allowed = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
        if len(v) != 26 or any(c not in allowed for c in v.upper()):
            raise ValueError("idempotency_key must be a 26-char ULID")
        return v


class ToolCallResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_name: str = Field(..., min_length=1)
    args_hash: str = Field(..., min_length=1)
    result_json: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    latency_ms: int = Field(..., ge=0)
    cost_usd_micros: int = Field(..., ge=0)
