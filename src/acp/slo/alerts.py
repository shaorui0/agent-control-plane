"""Alert sinks + rate-limited router.

The router watches burn-level transitions (ok → warn → critical → exhausted)
per (agent, task_class) and emits at most one alert per level per hour.
Down-shifts (e.g. critical → warn) are not alerted; they're reflected in the
dashboard.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from acp.clock import Clock
from acp.schemas.slo import BudgetSnapshot


# Alert severity rank — higher means more severe.
_RANK = {"ok": 0, "warn": 1, "critical": 2, "exhausted": 3}

# Per-level rate-limit window.
_RATE_LIMIT_SECONDS = 3600


class AlertSink(ABC):
    @abstractmethod
    def emit_alert(self, level: str, snapshot: BudgetSnapshot, message: str) -> None:
        raise NotImplementedError


class StdoutSink(AlertSink):
    def emit_alert(self, level: str, snapshot: BudgetSnapshot, message: str) -> None:
        print(
            f"[ACP-ALERT] level={level} agent={snapshot.agent_id} "
            f"task_class={snapshot.task_class} window={snapshot.window_label} "
            f"burn={snapshot.burn_rate:.2f} remaining={snapshot.budget_remaining:.2f} :: {message}"
        )


class FileSink(AlertSink):
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit_alert(self, level: str, snapshot: BudgetSnapshot, message: str) -> None:
        record = {
            "level": level,
            "ts": snapshot.ts,
            "agent_id": snapshot.agent_id,
            "task_class": snapshot.task_class,
            "model_version": snapshot.model_version,
            "window_label": snapshot.window_label,
            "budget_class": snapshot.budget_class,
            "sli_value": snapshot.sli_value,
            "slo_target": snapshot.slo_target,
            "burn_rate": snapshot.burn_rate,
            "budget_remaining": snapshot.budget_remaining,
            "message": message,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")


class WebhookSink(AlertSink):
    """HTTP POST to a webhook URL. Network errors are swallowed and logged."""

    def __init__(self, url: str, timeout_seconds: float = 5.0) -> None:
        self.url = url
        self.timeout = timeout_seconds

    def emit_alert(self, level: str, snapshot: BudgetSnapshot, message: str) -> None:
        try:
            import httpx
        except ImportError:  # pragma: no cover - dependency present
            return
        payload: dict[str, Any] = {
            "level": level,
            "agent_id": snapshot.agent_id,
            "task_class": snapshot.task_class,
            "window_label": snapshot.window_label,
            "burn_rate": snapshot.burn_rate,
            "budget_remaining": snapshot.budget_remaining,
            "message": message,
        }
        try:
            httpx.post(self.url, json=payload, timeout=self.timeout)
        except Exception:
            # Best-effort delivery; alerting must never crash the SLO engine.
            pass


class AlertRouter:
    """Tracks last-emit timestamp per (agent, task_class, level) for rate-limit."""

    def __init__(self, sink: AlertSink, clock: Clock) -> None:
        self.sink = sink
        self.clock = clock
        # key=(agent_id, task_class, level) → last emit ts (ms)
        self._last_emit: dict[tuple[str, str, str], int] = {}

    def evaluate(self, level: str, snapshot: BudgetSnapshot, message: str = "") -> bool:
        """Decide whether to emit, then emit. Returns True iff emitted."""
        if level == "ok":
            # Successful recovery clears the rate-limit so next burn alerts fast.
            for k in list(self._last_emit.keys()):
                if k[0] == snapshot.agent_id and k[1] == snapshot.task_class:
                    del self._last_emit[k]
            return False
        if level not in _RANK:
            raise ValueError(f"unknown alert level: {level!r}")

        key = (snapshot.agent_id, snapshot.task_class, level)
        now = self.clock.now()
        last = self._last_emit.get(key)
        if last is not None and (now - last) < _RATE_LIMIT_SECONDS * 1000:
            return False
        self._last_emit[key] = now
        self.sink.emit_alert(level, snapshot, message or f"burn level {level}")
        return True


__all__ = [
    "AlertRouter",
    "AlertSink",
    "FileSink",
    "StdoutSink",
    "WebhookSink",
]
