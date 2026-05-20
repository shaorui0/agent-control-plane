"""Earned Autonomy Gradient — server-side tier controller.

Per MASTER_PLAN.md axiom 8: gradient is a live daemon. Auto-contracts on burn,
auto-promotes only on clean stretches via manual operator trigger.
Per axiom 9: tier is computed server-side and never self-attested.

Public surface:
    - AutonomyController: read/mutate autonomy_state per (agent, task_class).
    - states: TIER_ORDER, TIER_DESCRIPTIONS, tier_index, from_index.
    - transitions: auto_demote_on_burn, evaluate_promotion_eligibility,
      evaluate_outcome_signals, run_autonomy_tick.
    - events.emit_autonomy_change: wide_event audit trail.
"""

from __future__ import annotations

from acp.autonomy.controller import AutonomyController
from acp.autonomy.events import emit_autonomy_change
from acp.autonomy.states import (
    TIER_DESCRIPTIONS,
    TIER_ORDER,
    AutonomyTier,
    from_index,
    tier_index,
)
from acp.autonomy.transitions import (
    auto_demote_on_burn,
    evaluate_burn_state,
    evaluate_outcome_signals,
    evaluate_promotion_eligibility,
    run_autonomy_tick,
)

__all__ = [
    "AutonomyController",
    "AutonomyTier",
    "TIER_DESCRIPTIONS",
    "TIER_ORDER",
    "tier_index",
    "from_index",
    "auto_demote_on_burn",
    "evaluate_burn_state",
    "evaluate_outcome_signals",
    "evaluate_promotion_eligibility",
    "run_autonomy_tick",
    "emit_autonomy_change",
]
