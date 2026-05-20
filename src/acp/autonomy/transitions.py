"""Gradient logic: burn-rate classification, demotion, promotion eligibility.

Demotion paths (all automatic, immediate):
  * burn level critical → -1 tier
  * burn level exhausted → -2 tiers (clamped at T0)
  * any harm=true judgment in the last hour → snap to T1
  * outcome cluster (>2 rollback_required / oncall_refire in 24h) → -1 tier

Promotion (manual + asymmetric):
  * requires N consecutive passes AND >= pass_rate AND >= window_hours stable.
  * eligibility is read-only; the actual transition is invoked from the CLI.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import TYPE_CHECKING

from acp.autonomy.states import (
    AutonomyTier,
    from_index,
    tier_index,
)
from acp.schemas.autonomy import AutonomyTierChange
from acp.schemas.slo import BudgetSnapshot

if TYPE_CHECKING:
    from acp.autonomy.controller import AutonomyController
    from acp.events.query import EventQuery


# Burn-rate thresholds. These match SLO engine conventions: burn_rate is the
# multiplier of the steady-state error budget consumption. >2x = warn, >5x =
# critical, >10x = exhausted (budget will be gone within the window).
_BURN_WARN = 2.0
_BURN_CRITICAL = 5.0
_BURN_EXHAUSTED = 10.0


def evaluate_burn_state(
    snapshots: Iterable[BudgetSnapshot],
    agent_id: str,
    task_class: str,
) -> str:
    """Classify the worst burn for one (agent, task_class) across snapshots.

    Returns "stable" / "warn" / "critical" / "exhausted". If no snapshots
    match, returns "stable" — absence of signal is not failure.
    """
    worst = "stable"
    order = {"stable": 0, "warn": 1, "critical": 2, "exhausted": 3}
    for s in snapshots:
        if s.agent_id != agent_id or s.task_class != task_class:
            continue
        level = _classify(s.burn_rate, s.budget_remaining)
        if order[level] > order[worst]:
            worst = level
    return worst


def _classify(burn_rate: float, budget_remaining: float) -> str:
    if budget_remaining <= 0.0:
        return "exhausted"
    if burn_rate >= _BURN_EXHAUSTED:
        return "exhausted"
    if burn_rate >= _BURN_CRITICAL:
        return "critical"
    if burn_rate >= _BURN_WARN:
        return "warn"
    return "stable"


# ---- promotion eligibility --------------------------------------------------


def evaluate_promotion_eligibility(
    query: "EventQuery",
    agent_id: str,
    task_class: str,
    current_tier: AutonomyTier,
    *,
    min_consecutive_pass: int = 100,
    min_pass_rate: float = 0.97,
    min_window_hours: int = 72,
    now_ms: int | None = None,
) -> bool:
    """Return True iff the (agent, task_class) qualifies for one tier up.

    Conditions (ALL must hold):
      * At least `min_consecutive_pass` recent judgments are present.
      * The most recent N judgments are all 'pass' (no breaks).
      * Overall pass rate over the window >= min_pass_rate.
      * The judgment stream spans at least `min_window_hours` of wall time.
      * Current tier is not already T4.

    Uses raw SQL because EventQuery doesn't expose judgments-by-recency.
    """
    if current_tier == AutonomyTier.T4:
        return False

    conn: sqlite3.Connection = query.conn  # type: ignore[attr-defined]

    # Pull the most recent judgment events for this (agent, task_class).
    rows = conn.execute(
        "SELECT ts, attrs_json FROM wide_events "
        "WHERE agent_id = ? AND task_class = ? AND event_type = 'judgment' "
        "ORDER BY ts DESC LIMIT ?",
        (agent_id, task_class, max(min_consecutive_pass * 4, min_consecutive_pass)),
    ).fetchall()

    if len(rows) < min_consecutive_pass:
        return False

    verdicts: list[str] = []
    timestamps: list[int] = []
    for r in rows:
        try:
            attrs = json.loads(r["attrs_json"] or "{}")
        except (TypeError, ValueError):
            attrs = {}
        verdicts.append(str(attrs.get("verdict", "")))
        timestamps.append(int(r["ts"]))

    # Most recent N (rows are DESC) must all be pass.
    recent = verdicts[:min_consecutive_pass]
    if any(v != "pass" for v in recent):
        return False

    # Pass rate over the sample.
    passes = sum(1 for v in verdicts if v == "pass")
    pass_rate = passes / len(verdicts)
    if pass_rate < min_pass_rate:
        return False

    # Window span — newest minus oldest among the sampled rows.
    span_ms = max(timestamps) - min(timestamps)
    min_span_ms = min_window_hours * 3600 * 1000
    if span_ms < min_span_ms:
        return False

    return True


# ---- automatic demotion -----------------------------------------------------


def auto_demote_on_burn(
    controller: "AutonomyController",
    snapshots: Iterable[BudgetSnapshot],
) -> list[AutonomyTierChange]:
    """Apply burn-based demotions + harm-judgment snap-downs.

    Snapshots are typically the latest per (agent, task_class, budget_class).
    Both organic and adversarial budget classes are evaluated independently;
    either can demote. Returns the list of changes actually applied.
    """
    changes: list[AutonomyTierChange] = []
    snaps = list(snapshots)

    # Group by (agent_id, task_class) and pick the worst burn across budget
    # classes — adversarial burn alone is enough to demote.
    pairs: dict[tuple[str, str], list[BudgetSnapshot]] = {}
    for s in snaps:
        pairs.setdefault((s.agent_id, s.task_class), []).append(s)

    for (agent_id, task_class), group in pairs.items():
        # Harm judgment in last hour → snap to T1 (overrides burn).
        if _recent_harm(controller, agent_id, task_class):
            current = controller.current_tier(agent_id, task_class)
            if tier_index(current) > tier_index(AutonomyTier.T1):
                changes.append(
                    controller.apply_demotion(
                        agent_id,
                        task_class,
                        AutonomyTier.T1,
                        reason="harm_judgment_last_hour",
                    )
                )
            continue

        # Worst burn level across budget classes in this group.
        worst_level = "stable"
        worst_burn: float | None = None
        order = {"stable": 0, "warn": 1, "critical": 2, "exhausted": 3}
        for s in group:
            level = _classify(s.burn_rate, s.budget_remaining)
            if order[level] > order[worst_level]:
                worst_level = level
                worst_burn = s.burn_rate

        if worst_level in ("stable", "warn"):
            continue

        step = 1 if worst_level == "critical" else 2
        current = controller.current_tier(agent_id, task_class)
        new_idx = tier_index(current) - step
        new_tier = from_index(new_idx)
        if new_tier == current:
            continue  # already at the floor

        change = controller.apply_demotion(
            agent_id,
            task_class,
            new_tier,
            reason=f"burn_{worst_level}",
        )
        if worst_burn is not None:
            # Re-emit with burn_rate attached for richer audit context.
            change = AutonomyTierChange(
                agent_id=change.agent_id,
                task_class=change.task_class,
                old_tier=change.old_tier,
                new_tier=change.new_tier,
                cause=change.cause,
                burn_rate=worst_burn,
                ts=change.ts,
            )
        changes.append(change)

    return changes


def _recent_harm(
    controller: "AutonomyController",
    agent_id: str,
    task_class: str,
) -> bool:
    """True if any judgment with harm=true was emitted in the last hour."""
    now_ms = controller.clock.now()
    since_ms = now_ms - 3600 * 1000
    rows = controller.conn.execute(
        "SELECT attrs_json FROM wide_events "
        "WHERE agent_id = ? AND task_class = ? AND event_type = 'judgment' "
        "AND ts >= ?",
        (agent_id, task_class, since_ms),
    ).fetchall()
    for r in rows:
        try:
            attrs = json.loads(r["attrs_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if attrs.get("harm") is True:
            return True
    return False


# ---- outcome-signal demotion -----------------------------------------------


def evaluate_outcome_signals(
    controller: "AutonomyController",
    conn: sqlite3.Connection,
) -> list[AutonomyTierChange]:
    """Demote when external outcome signals cluster.

    Trigger: >2 rollback_required or oncall_refire signals in the last 24h
    for the same (agent, task_class). Drops the tier by one. Operates on
    `outcome_signals` joined to `wide_events` via run_id to recover the
    (agent, task_class) — outcome rows alone don't carry that.
    """
    changes: list[AutonomyTierChange] = []
    now_ms = controller.clock.now()
    since_ms = now_ms - 24 * 3600 * 1000

    rows = conn.execute(
        """
        SELECT we.agent_id AS agent_id,
               we.task_class AS task_class,
               COUNT(*) AS n
        FROM outcome_signals os
        JOIN wide_events we ON we.run_id = os.run_id
        WHERE os.kind IN ('rollback_required', 'oncall_refire')
          AND os.ts >= ?
        GROUP BY we.agent_id, we.task_class
        HAVING n > 2
        """,
        (since_ms,),
    ).fetchall()

    for r in rows:
        agent_id = r["agent_id"]
        task_class = r["task_class"]
        current = controller.current_tier(agent_id, task_class)
        new_tier = from_index(tier_index(current) - 1)
        if new_tier == current:
            continue
        changes.append(
            controller.apply_demotion(
                agent_id,
                task_class,
                new_tier,
                reason=f"outcome_cluster:{r['n']}_in_24h",
            )
        )
    return changes


# ---- tick orchestration -----------------------------------------------------


def _load_latest_snapshots(conn: sqlite3.Connection) -> list[BudgetSnapshot]:
    """Most-recent snapshot per (agent_id, task_class, budget_class, window_label)."""
    rows = conn.execute(
        """
        SELECT s.*
        FROM slo_snapshots s
        JOIN (
            SELECT agent_id, task_class, budget_class, window_label,
                   MAX(ts) AS max_ts
            FROM slo_snapshots
            GROUP BY agent_id, task_class, budget_class, window_label
        ) latest
          ON s.agent_id = latest.agent_id
         AND s.task_class = latest.task_class
         AND s.budget_class = latest.budget_class
         AND s.window_label = latest.window_label
         AND s.ts = latest.max_ts
        """,
    ).fetchall()
    out: list[BudgetSnapshot] = []
    for r in rows:
        out.append(
            BudgetSnapshot(
                snapshot_id=r["snapshot_id"],
                ts=r["ts"],
                agent_id=r["agent_id"],
                task_class=r["task_class"],
                model_version=r["model_version"],
                window_label=r["window_label"],
                budget_class=r["budget_class"],
                sli_value=r["sli_value"],
                slo_target=r["slo_target"],
                burn_rate=r["burn_rate"],
                budget_remaining=r["budget_remaining"],
            )
        )
    return out


def run_autonomy_tick(
    controller: "AutonomyController",
    slo_engine: object | None,
    query: "EventQuery | None",
    conn: sqlite3.Connection,
) -> list[AutonomyTierChange]:
    """One pass of the autonomy daemon. Scheduled every 60s by W5A.

    1. Load latest BudgetSnapshots from slo_snapshots.
    2. Auto-demote on burn (+ harm judgments).
    3. Auto-demote on outcome-signal clusters.
    4. Never auto-promotes — promotion is operator-driven via CLI.
    """
    snapshots = _load_latest_snapshots(conn)

    changes: list[AutonomyTierChange] = []
    changes.extend(auto_demote_on_burn(controller, snapshots))
    changes.extend(evaluate_outcome_signals(controller, conn))
    return changes


__all__ = [
    "evaluate_burn_state",
    "evaluate_promotion_eligibility",
    "auto_demote_on_burn",
    "evaluate_outcome_signals",
    "run_autonomy_tick",
]
