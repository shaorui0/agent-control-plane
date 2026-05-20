"""AgentDecision — the structured pre-action log from the agent."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .tool import ToolCallRequest


class AgentDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_hash: str = Field(..., min_length=1)
    reasoning: str = ""
    chosen_action: ToolCallRequest | None = None
    final_answer: str | None = None
    self_confidence: float = Field(..., ge=0.0, le=1.0)
    tokens_in: int = Field(..., ge=0)
    tokens_out: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _exactly_one(self) -> "AgentDecision":
        has_action = self.chosen_action is not None
        has_final = self.final_answer is not None
        if has_action == has_final:
            raise ValueError("exactly one of chosen_action / final_answer must be set")
        return self
