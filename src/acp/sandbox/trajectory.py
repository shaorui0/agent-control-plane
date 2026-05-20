"""Trajectory — per-run step counter with hard cap.

Use as a context manager; `next_step()` advances and raises BudgetExceeded
if the step would exceed max_steps. Default cap is 20 (defense vs runaway loops).
"""

from __future__ import annotations

from dataclasses import dataclass

from acp.errors import BudgetExceeded


@dataclass
class Trajectory:
    run_id: str
    max_steps: int = 20
    _step: int = 0

    def __enter__(self) -> "Trajectory":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    @property
    def step(self) -> int:
        return self._step

    def next_step(self) -> int:
        self._step += 1
        if self._step > self.max_steps:
            raise BudgetExceeded("max_steps", float(self.max_steps), float(self._step))
        return self._step
