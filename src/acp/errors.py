"""Typed ACP errors. Messages stay internal; only reason_code is agent-visible."""
from __future__ import annotations


class ACPError(Exception):
    """Base for all ACP-internal errors. Carries an internal message."""


class DenyClosed(ACPError):
    """Policy / budget / intent denial. Fail-closed by design.

    `reason_code` is a short stable token safe to surface to the agent.
    The detailed `message` MUST NOT be serialized agent-facing.
    """

    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.message = message

    def to_agent_dict(self) -> dict[str, str]:
        """Agent-facing serialization: reason_code only, no internal detail."""
        return {"status": "denied", "reason_code": self.reason_code}


class IntegrityError(ACPError):
    """Event chain or DB integrity violation. Operator-facing only."""


class BudgetExceeded(ACPError):
    """A budget cap (tokens / dollars / steps / wall) was hit."""

    def __init__(self, kind: str, limit: float, observed: float) -> None:
        super().__init__(f"budget {kind} exceeded: {observed} > {limit}")
        self.kind = kind
        self.limit = limit
        self.observed = observed
