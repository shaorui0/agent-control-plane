"""Injectable Clock — tests use FrozenClock; production uses RealClock."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol


class Clock(Protocol):
    def now(self) -> int:  # ms since epoch
        ...


class RealClock:
    """Wall-clock time in milliseconds."""

    def now(self) -> int:
        return time.time_ns() // 1_000_000


@dataclass
class FrozenClock:
    """A fixed clock that can be advanced; for deterministic tests."""

    at_ms: int

    def now(self) -> int:
        return self.at_ms

    def advance(self, delta_ms: int) -> None:
        self.at_ms += delta_ms


_DEFAULT: Clock = RealClock()


def default_clock() -> Clock:
    return _DEFAULT
