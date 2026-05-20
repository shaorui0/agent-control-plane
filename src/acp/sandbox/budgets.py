"""Sandbox budget helpers — step + wall-clock guards.

Re-exports `BudgetExceeded` so callers don't have to reach into `acp.errors`.
"""

from __future__ import annotations

from dataclasses import dataclass

from acp.errors import BudgetExceeded

__all__ = ["BudgetExceeded", "StepBudget"]


@dataclass
class StepBudget:
    """Tiny counter that raises when steps exceed cap.

    Distinct from Trajectory (which is per-run); StepBudget is generic and
    composable for sub-budgets like "tool calls per phase".
    """

    cap: int
    used: int = 0

    def consume(self, n: int = 1) -> None:
        self.used += n
        if self.used > self.cap:
            raise BudgetExceeded("steps", float(self.cap), float(self.used))

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.used)
