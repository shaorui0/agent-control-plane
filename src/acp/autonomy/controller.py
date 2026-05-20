"""AutonomyController — read/mutate per-(agent, task_class) tier state.

Tier state lives in `autonomy_state`. Reads never raise: if a row is missing we
fall back to the registered spec's default_tier so callers always get a usable
answer. Writes are explicit demotion/promotion methods; promotions require an
operator signature (asymmetric defaults per axiom 8).
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from acp.autonomy.events import emit_autonomy_change
from acp.autonomy.states import AutonomyTier
from acp.clock import Clock, default_clock
from acp.schemas.autonomy import AutonomyTierChange

if TYPE_CHECKING:
    from acp.events.store import WideEventStore
    from acp.registry.store import RegistryStore


class AutonomyController:
    """Owns the autonomy_state table.

    Demotion is automatic + immediate (no operator). Promotion is asymmetric:
    it requires an explicit operator identity, typically a CLI-driven manual
    trigger after evaluate_promotion_eligibility passes.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        event_store: "WideEventStore",
        registry_store: "RegistryStore | None" = None,
        clock: Clock | None = None,
    ) -> None:
        self.conn = conn
        self.event_store = event_store
        self.registry_store = registry_store
        self.clock = clock or default_clock()

    # ---- read path --------------------------------------------------

    def current_tier(self, agent_id: str, task_class: str) -> AutonomyTier:
        """Return live tier; falls back to spec default; never raises."""
        row = self.conn.execute(
            "SELECT current_tier FROM autonomy_state WHERE agent_id=? AND task_class=?",
            (agent_id, task_class),
        ).fetchone()
        if row is not None:
            try:
                return AutonomyTier(row["current_tier"])
            except ValueError:
                pass  # corrupt row → fall through to default
        return self._default_tier(agent_id)

    def _default_tier(self, agent_id: str) -> AutonomyTier:
        if self.registry_store is not None:
            spec = self.registry_store.get(agent_id)
            if spec is not None:
                return spec.default_tier
        return AutonomyTier.T1

    # ---- initialization ---------------------------------------------

    def initialize_for_agent(self, agent_id: str) -> None:
        """Ensure one autonomy_state row per task_class at default_tier."""
        if self.registry_store is None:
            return
        spec = self.registry_store.get(agent_id)
        if spec is None:
            return
        ts = self.clock.now()
        with self.conn:
            for tc in spec.task_classes:
                self.conn.execute(
                    "INSERT OR IGNORE INTO autonomy_state "
                    "(agent_id, task_class, current_tier, since, last_reason) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (agent_id, tc.name, spec.default_tier.value, ts, "initialized"),
                )

    # ---- mutation ---------------------------------------------------

    def apply_demotion(
        self,
        agent_id: str,
        task_class: str,
        new_tier: AutonomyTier,
        reason: str,
    ) -> AutonomyTierChange:
        """Apply an automatic demotion. Returns the recorded change.

        Demotion is asymmetric: no operator required. If the agent is already
        at or below `new_tier`, this is a no-op (we still emit, to make the
        audit log explicit about evaluator decisions).
        """
        return self._apply(
            agent_id=agent_id,
            task_class=task_class,
            new_tier=new_tier,
            reason=reason,
            operator=None,
            is_promotion=False,
        )

    def apply_promotion(
        self,
        agent_id: str,
        task_class: str,
        new_tier: AutonomyTier,
        reason: str,
        operator: str | None,
    ) -> AutonomyTierChange:
        """Apply a manual promotion. Requires an operator identity.

        Operator must be a non-empty string (typically the CLI user's email or
        principal). Anything else raises ValueError so callers cannot bypass
        the asymmetric default.
        """
        if not operator or not operator.strip():
            raise ValueError(
                "promotion requires an operator signature; auto-promotion is forbidden"
            )
        return self._apply(
            agent_id=agent_id,
            task_class=task_class,
            new_tier=new_tier,
            reason=reason,
            operator=operator,
            is_promotion=True,
        )

    # ---- internals --------------------------------------------------

    def _apply(
        self,
        *,
        agent_id: str,
        task_class: str,
        new_tier: AutonomyTier,
        reason: str,
        operator: str | None,
        is_promotion: bool,
    ) -> AutonomyTierChange:
        old_tier = self.current_tier(agent_id, task_class)
        ts = self.clock.now()

        with self.conn:
            self.conn.execute(
                "INSERT INTO autonomy_state "
                "(agent_id, task_class, current_tier, since, last_reason) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(agent_id, task_class) DO UPDATE SET "
                "current_tier=excluded.current_tier, "
                "since=excluded.since, "
                "last_reason=excluded.last_reason",
                (agent_id, task_class, new_tier.value, ts, reason),
            )

        change = AutonomyTierChange(
            agent_id=agent_id,
            task_class=task_class,
            old_tier=old_tier,
            new_tier=new_tier,
            cause=reason,
            ts=ts,
        )
        emit_autonomy_change(self.event_store, change, operator=operator)
        return change


__all__ = ["AutonomyController"]
