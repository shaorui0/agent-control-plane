"""SLOEngine — periodic SLI/burn/budget evaluator.

Every tick (default 60s) we walk the SLODefinitionRegistry, compute the SLI +
burn rate + budget remaining for every (agent, task_class, model_version,
window) combination on both the organic and adversarial slices, and append the
result to `slo_snapshots`. Snapshots are immutable; the SLO dashboard reads the
most recent row per (agent, task_class, window, budget_class).

The same tick also runs `silent_failure_detector` so silent K2 failures get
swept into the audit trail without operator intervention.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from acp.clock import Clock
from acp.events.query import EventQuery
from acp.events.sli import (
    intervention_free_rate,
    judge_pass_rate,
    p95_latency_ms,
    silent_fail_rate,
)
from acp.ids import new_ulid
from acp.schemas.agent import BudgetClass, SliKind
from acp.schemas.slo import BudgetSnapshot, SLODefinition, WindowLabel
from acp.slo.budget import budget_remaining
from acp.slo.burnrate import (
    BURN_WINDOWS,
    burn_rate,
)
from acp.slo.definitions import SLODefinitionRegistry
from acp.slo.feedback import silent_failure_detector

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler


def _sli_value(
    kind: SliKind,
    query: EventQuery,
    agent_id: str,
    task_class: str,
    model_version: str,
    window_seconds: int,
    clock: Clock,
) -> float:
    fn_map = {
        "judge_pass_rate": judge_pass_rate,
        "intervention_free_rate": intervention_free_rate,
        "silent_fail_rate": silent_fail_rate,
        "p95_latency_ms": p95_latency_ms,
    }
    return fn_map[kind](query, agent_id, task_class, model_version, window_seconds, clock)


def _goodness(kind: SliKind, raw: float, target: float) -> tuple[float, float]:
    """Translate raw SLI + raw target into ([0,1] goodness, [0,1) goodness target)."""
    if kind in ("judge_pass_rate", "intervention_free_rate"):
        return max(0.0, min(1.0, raw)), max(0.0, min(0.999, target))
    if kind == "silent_fail_rate":
        return max(0.0, min(1.0, 1.0 - raw)), max(0.0, min(0.999, 1.0 - target))
    if kind == "p95_latency_ms":
        if raw <= 0:
            return 1.0, 0.99
        return max(0.0, min(1.0, target / raw if target > 0 else 0.0)), 0.99
    raise ValueError(f"unknown sli kind {kind!r}")


class SLOEngine:
    def __init__(
        self,
        conn: sqlite3.Connection,
        query: EventQuery,
        registry_store,
        definitions_registry: SLODefinitionRegistry,
        clock: Clock,
    ) -> None:
        self.conn = conn
        self.query = query
        self.registry_store = registry_store
        self.defs = definitions_registry
        self.clock = clock
        self._scheduler: "BackgroundScheduler | None" = None

    # -- main loop ---------------------------------------------------------

    def evaluate_all(self) -> list[BudgetSnapshot]:
        """Recompute every (definition × window) snapshot. Writes + returns them."""
        snapshots: list[BudgetSnapshot] = []
        now = self.clock.now()

        # Dedupe SLO definitions on (agent, task_class, model_version, budget_class).
        seen: set[tuple[str, str, str, BudgetClass]] = set()
        for d in self.defs.all_definitions(include_adversarial=True):
            key = (d.agent_id, d.task_class, d.model_version, d.budget_class)
            if key in seen:
                continue
            seen.add(key)
            for label, window_s in BURN_WINDOWS:
                snapshot = self._evaluate_one(d, label, window_s, now)
                snapshots.append(snapshot)

        self._persist(snapshots)
        return snapshots

    def _evaluate_one(
        self,
        d: SLODefinition,
        label: WindowLabel,
        window_s: int,
        now_ms: int,
    ) -> BudgetSnapshot:
        raw = _sli_value(
            d.sli_kind,
            self.query,
            d.agent_id,
            d.task_class,
            d.model_version,
            window_s,
            self.clock,
        )
        goodness, goodness_target = _goodness(d.sli_kind, raw, d.target)
        br = burn_rate(goodness, goodness_target)
        rem = budget_remaining(goodness, goodness_target, window_s)
        return BudgetSnapshot(
            snapshot_id=new_ulid(),
            ts=now_ms,
            agent_id=d.agent_id,
            task_class=d.task_class,
            model_version=d.model_version,
            window_label=label,
            budget_class=d.budget_class,
            sli_value=goodness,
            slo_target=goodness_target,
            burn_rate=br,
            budget_remaining=rem,
        )

    def _persist(self, snaps: list[BudgetSnapshot]) -> None:
        if not snaps:
            return
        rows = [
            (
                s.snapshot_id,
                s.ts,
                s.agent_id,
                s.task_class,
                s.model_version,
                s.window_label,
                s.budget_class,
                s.sli_value,
                s.slo_target,
                s.burn_rate,
                s.budget_remaining,
            )
            for s in snaps
        ]
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO slo_snapshots (
                    snapshot_id, ts, agent_id, task_class, model_version,
                    window_label, budget_class, sli_value, slo_target, burn_rate,
                    budget_remaining
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    # -- scheduling --------------------------------------------------------

    def tick(self) -> None:
        """One scheduled tick: evaluate snapshots + run silent-failure sweep."""
        self.evaluate_all()
        silent_failure_detector(self.conn, self.clock)

    def start_scheduler(self, interval_seconds: int = 60) -> "BackgroundScheduler":
        """Start an APScheduler BackgroundScheduler. Returns the scheduler so the
        caller can shut it down at process exit."""
        from apscheduler.schedulers.background import BackgroundScheduler

        sch = BackgroundScheduler()
        sch.add_job(self.tick, "interval", seconds=interval_seconds, id="slo-engine-tick")
        sch.start()
        self._scheduler = sch
        return sch
