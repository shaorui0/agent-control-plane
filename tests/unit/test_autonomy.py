"""Unit tests for the autonomy gradient (Wave 4C)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pytest

from acp.autonomy.controller import AutonomyController
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
from acp.clock import FrozenClock
from acp.events.query import EventQuery
from acp.events.store import WideEventStore
from acp.ids import new_ulid
from acp.schemas.agent import AgentSpec, TaskClassConfig, ToolBinding
from acp.schemas.slo import BudgetSnapshot


AGENT = "oncall-triage"
TASK = "triage"
MODEL = "claude-sonnet-4.6@2026-05-01"


# ---- fakes ------------------------------------------------------------------


@dataclass
class _FakeRegistry:
    spec: AgentSpec | None

    def get(self, agent_id: str) -> AgentSpec | None:
        if self.spec and self.spec.agent_id == agent_id:
            return self.spec
        return None


def _spec(default_tier: AutonomyTier = AutonomyTier.T3) -> AgentSpec:
    return AgentSpec(
        agent_id=AGENT,
        owner="rshao@datavisor.com",
        version="1.0.0",
        model_version=MODEL,
        description="t",
        task_classes=[
            TaskClassConfig(
                name=TASK,
                slo_sli_kind="judge_pass_rate",
                slo_target=0.97,
                slo_window="7d",
            )
        ],
        sealed_tools=[
            ToolBinding(name="kubectl_scale", max_tier=AutonomyTier.T3, requires_intent=True),
        ],
        budget_hourly_usd=1.0,
        budget_hourly_tokens=10_000,
        default_tier=default_tier,
    )


def _make_controller(
    conn: sqlite3.Connection,
    clock: FrozenClock,
    default_tier: AutonomyTier = AutonomyTier.T3,
) -> tuple[AutonomyController, WideEventStore, _FakeRegistry]:
    store = WideEventStore(conn, clock=clock)
    reg = _FakeRegistry(spec=_spec(default_tier))
    ctrl = AutonomyController(conn, store, registry_store=reg, clock=clock)
    return ctrl, store, reg


def _snapshot(
    *,
    burn_rate: float,
    budget_remaining: float = 0.5,
    budget_class: str = "organic",
    window_label: str = "1h",
    ts: int = 0,
    agent_id: str = AGENT,
    task_class: str = TASK,
) -> BudgetSnapshot:
    return BudgetSnapshot(
        snapshot_id=new_ulid(),
        ts=ts,
        agent_id=agent_id,
        task_class=task_class,
        model_version=MODEL,
        window_label=window_label,
        budget_class=budget_class,
        sli_value=0.95,
        slo_target=0.97,
        burn_rate=burn_rate,
        budget_remaining=budget_remaining,
    )


# ---- states -----------------------------------------------------------------


def test_tier_order_and_index():
    assert TIER_ORDER == [
        AutonomyTier.T0,
        AutonomyTier.T1,
        AutonomyTier.T2,
        AutonomyTier.T3,
        AutonomyTier.T4,
    ]
    assert tier_index(AutonomyTier.T0) == 0
    assert tier_index(AutonomyTier.T4) == 4
    assert from_index(2) == AutonomyTier.T2
    assert from_index(-5) == AutonomyTier.T0
    assert from_index(99) == AutonomyTier.T4
    assert set(TIER_DESCRIPTIONS.keys()) == set(TIER_ORDER)


# ---- controller -------------------------------------------------------------


def test_current_tier_falls_back_to_default(tmp_db, frozen_clock):
    ctrl, _, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T2)
    assert ctrl.current_tier(AGENT, TASK) == AutonomyTier.T2


def test_apply_demotion_updates_state(tmp_db, frozen_clock):
    ctrl, _, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T3)
    change = ctrl.apply_demotion(AGENT, TASK, AutonomyTier.T1, reason="manual_test")
    assert change.old_tier == AutonomyTier.T3
    assert change.new_tier == AutonomyTier.T1
    assert ctrl.current_tier(AGENT, TASK) == AutonomyTier.T1


def test_apply_demotion_emits_wide_event(tmp_db, frozen_clock):
    ctrl, store, _ = _make_controller(tmp_db, frozen_clock)
    ctrl.apply_demotion(AGENT, TASK, AutonomyTier.T1, reason="burn_critical")
    rows = tmp_db.execute(
        "SELECT event_type, attrs_json FROM wide_events WHERE event_type='autonomy_change'"
    ).fetchall()
    assert len(rows) == 1


def test_apply_promotion_requires_operator(tmp_db, frozen_clock):
    ctrl, _, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T1)
    with pytest.raises(ValueError):
        ctrl.apply_promotion(AGENT, TASK, AutonomyTier.T2, reason="ok", operator=None)
    with pytest.raises(ValueError):
        ctrl.apply_promotion(AGENT, TASK, AutonomyTier.T2, reason="ok", operator="")


def test_apply_promotion_with_operator_works(tmp_db, frozen_clock):
    ctrl, _, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T1)
    change = ctrl.apply_promotion(
        AGENT, TASK, AutonomyTier.T2, reason="eligible", operator="rshao@datavisor.com"
    )
    assert change.new_tier == AutonomyTier.T2
    assert ctrl.current_tier(AGENT, TASK) == AutonomyTier.T2


def test_initialize_for_agent_seeds_rows(tmp_db, frozen_clock):
    ctrl, _, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T2)
    ctrl.initialize_for_agent(AGENT)
    row = tmp_db.execute(
        "SELECT current_tier FROM autonomy_state WHERE agent_id=? AND task_class=?",
        (AGENT, TASK),
    ).fetchone()
    assert row is not None
    assert row["current_tier"] == "T2"


# ---- burn classification ----------------------------------------------------


def test_evaluate_burn_state_levels():
    snaps_stable = [_snapshot(burn_rate=1.0)]
    snaps_warn = [_snapshot(burn_rate=3.0)]
    snaps_crit = [_snapshot(burn_rate=6.0)]
    snaps_exh = [_snapshot(burn_rate=20.0)]
    snaps_exh2 = [_snapshot(burn_rate=1.0, budget_remaining=0.0)]
    assert evaluate_burn_state(snaps_stable, AGENT, TASK) == "stable"
    assert evaluate_burn_state(snaps_warn, AGENT, TASK) == "warn"
    assert evaluate_burn_state(snaps_crit, AGENT, TASK) == "critical"
    assert evaluate_burn_state(snaps_exh, AGENT, TASK) == "exhausted"
    assert evaluate_burn_state(snaps_exh2, AGENT, TASK) == "exhausted"


def test_evaluate_burn_state_takes_worst():
    snaps = [_snapshot(burn_rate=1.0), _snapshot(burn_rate=6.0), _snapshot(burn_rate=3.0)]
    assert evaluate_burn_state(snaps, AGENT, TASK) == "critical"


# ---- auto-demote ------------------------------------------------------------


def test_auto_demote_critical_one_tier(tmp_db, frozen_clock):
    ctrl, _, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T3)
    changes = auto_demote_on_burn(ctrl, [_snapshot(burn_rate=6.0)])
    assert len(changes) == 1
    assert changes[0].new_tier == AutonomyTier.T2
    assert ctrl.current_tier(AGENT, TASK) == AutonomyTier.T2


def test_auto_demote_exhausted_two_tiers_clamped(tmp_db, frozen_clock):
    ctrl, _, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T3)
    changes = auto_demote_on_burn(ctrl, [_snapshot(burn_rate=50.0)])
    assert len(changes) == 1
    assert changes[0].new_tier == AutonomyTier.T1

    # From T1 → exhausted again should clamp at T0, not below.
    ctrl.apply_demotion(AGENT, TASK, AutonomyTier.T1, reason="reset")
    changes2 = auto_demote_on_burn(ctrl, [_snapshot(burn_rate=50.0)])
    assert len(changes2) == 1
    assert changes2[0].new_tier == AutonomyTier.T0

    # Already T0 → no-op.
    changes3 = auto_demote_on_burn(ctrl, [_snapshot(burn_rate=50.0)])
    assert changes3 == []


def test_auto_demote_organic_and_adversarial_independent(tmp_db, frozen_clock):
    ctrl, _, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T3)
    snaps = [
        _snapshot(burn_rate=1.0, budget_class="organic"),
        _snapshot(burn_rate=12.0, budget_class="adversarial"),
    ]
    changes = auto_demote_on_burn(ctrl, snaps)
    assert len(changes) == 1
    assert changes[0].new_tier == AutonomyTier.T1  # exhausted → -2


def test_auto_demote_harm_judgment_snaps_to_t1(tmp_db, frozen_clock):
    ctrl, store, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T3)
    store.emit(
        run_id="r1", agent_id=AGENT, task_class=TASK, model_version=MODEL,
        step=0, event_type="judgment",
        attrs={"verdict": "fail", "harm": True},
    )
    changes = auto_demote_on_burn(ctrl, [_snapshot(burn_rate=1.0)])
    assert len(changes) == 1
    assert changes[0].new_tier == AutonomyTier.T1
    assert "harm" in changes[0].cause


# ---- promotion eligibility --------------------------------------------------


def test_promotion_eligibility_all_conditions_met(tmp_db):
    clk = FrozenClock(at_ms=1_000_000_000_000)
    store = WideEventStore(tmp_db, clock=clk)

    # Spread 120 passing judgments across 80 hours.
    spread_ms = 80 * 3600 * 1000
    step = spread_ms // 120
    for i in range(120):
        clk.at_ms = 1_000_000_000_000 - spread_ms + i * step
        store.emit(
            run_id=f"r{i}", agent_id=AGENT, task_class=TASK, model_version=MODEL,
            step=0, event_type="judgment",
            attrs={"verdict": "pass"},
        )
    clk.at_ms = 1_000_000_000_000

    ok = evaluate_promotion_eligibility(
        EventQuery(tmp_db), AGENT, TASK, AutonomyTier.T2,
        min_consecutive_pass=100, min_pass_rate=0.97, min_window_hours=72,
    )
    assert ok is True


def test_promotion_eligibility_breaks_on_recent_fail(tmp_db):
    clk = FrozenClock(at_ms=1_000_000_000_000)
    store = WideEventStore(tmp_db, clock=clk)
    spread_ms = 80 * 3600 * 1000
    step = spread_ms // 120
    for i in range(120):
        clk.at_ms = 1_000_000_000_000 - spread_ms + i * step
        verdict = "fail" if i == 119 else "pass"  # most recent is fail
        store.emit(
            run_id=f"r{i}", agent_id=AGENT, task_class=TASK, model_version=MODEL,
            step=0, event_type="judgment",
            attrs={"verdict": verdict},
        )
    ok = evaluate_promotion_eligibility(
        EventQuery(tmp_db), AGENT, TASK, AutonomyTier.T2,
        min_consecutive_pass=100,
    )
    assert ok is False


def test_promotion_eligibility_too_few_judgments(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    for i in range(50):
        store.emit(
            run_id=f"r{i}", agent_id=AGENT, task_class=TASK, model_version=MODEL,
            step=0, event_type="judgment", attrs={"verdict": "pass"},
        )
    ok = evaluate_promotion_eligibility(
        EventQuery(tmp_db), AGENT, TASK, AutonomyTier.T2, min_consecutive_pass=100,
    )
    assert ok is False


def test_promotion_eligibility_window_too_short(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    # All judgments at the same instant → span 0.
    for i in range(120):
        store.emit(
            run_id=f"r{i}", agent_id=AGENT, task_class=TASK, model_version=MODEL,
            step=0, event_type="judgment", attrs={"verdict": "pass"},
        )
    ok = evaluate_promotion_eligibility(
        EventQuery(tmp_db), AGENT, TASK, AutonomyTier.T2,
        min_consecutive_pass=100, min_window_hours=72,
    )
    assert ok is False


def test_promotion_eligibility_t4_caps(tmp_db, frozen_clock):
    ok = evaluate_promotion_eligibility(
        EventQuery(tmp_db), AGENT, TASK, AutonomyTier.T4,
    )
    assert ok is False


# ---- outcome signals --------------------------------------------------------


def _insert_outcome(conn: sqlite3.Connection, run_id: str, kind: str, ts: int):
    conn.execute(
        "INSERT INTO outcome_signals "
        "(signal_id, run_id, kind, value_json, delay_seconds, source, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (new_ulid(), run_id, kind, "{}", 0, "test", ts),
    )


def test_outcome_signal_cluster_demotes(tmp_db, frozen_clock):
    ctrl, store, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T3)
    # Emit 3 task_start events so we have run_id → (agent, task_class) mapping.
    for i in range(3):
        store.emit(
            run_id=f"run-{i}", agent_id=AGENT, task_class=TASK, model_version=MODEL,
            step=0, event_type="task_start",
        )
        _insert_outcome(tmp_db, f"run-{i}", "rollback_required", frozen_clock.now())

    changes = evaluate_outcome_signals(ctrl, tmp_db)
    assert len(changes) == 1
    assert changes[0].new_tier == AutonomyTier.T2


def test_outcome_signal_below_threshold_no_demote(tmp_db, frozen_clock):
    ctrl, store, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T3)
    for i in range(2):  # only 2, threshold is >2
        store.emit(
            run_id=f"run-{i}", agent_id=AGENT, task_class=TASK, model_version=MODEL,
            step=0, event_type="task_start",
        )
        _insert_outcome(tmp_db, f"run-{i}", "rollback_required", frozen_clock.now())
    changes = evaluate_outcome_signals(ctrl, tmp_db)
    assert changes == []


# ---- run_autonomy_tick ------------------------------------------------------


def _insert_snapshot(conn: sqlite3.Connection, snap: BudgetSnapshot):
    conn.execute(
        "INSERT INTO slo_snapshots "
        "(snapshot_id, ts, agent_id, task_class, model_version, window_label, "
        " budget_class, sli_value, slo_target, burn_rate, budget_remaining) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snap.snapshot_id, snap.ts, snap.agent_id, snap.task_class,
            snap.model_version, snap.window_label, snap.budget_class,
            snap.sli_value, snap.slo_target, snap.burn_rate, snap.budget_remaining,
        ),
    )


def test_run_autonomy_tick_smoke(tmp_db, frozen_clock):
    ctrl, _, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T3)
    _insert_snapshot(
        tmp_db,
        _snapshot(burn_rate=6.0, ts=frozen_clock.now(), window_label="1h"),
    )
    changes = run_autonomy_tick(ctrl, slo_engine=None, query=None, conn=tmp_db)
    assert len(changes) == 1
    assert changes[0].new_tier == AutonomyTier.T2
    assert ctrl.current_tier(AGENT, TASK) == AutonomyTier.T2


def test_run_autonomy_tick_uses_latest_snapshot(tmp_db, frozen_clock):
    ctrl, _, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T3)
    # Older stale critical snapshot, newer stable one → should NOT demote.
    _insert_snapshot(
        tmp_db,
        _snapshot(burn_rate=20.0, ts=frozen_clock.now() - 1000, window_label="1h"),
    )
    _insert_snapshot(
        tmp_db,
        _snapshot(burn_rate=1.0, ts=frozen_clock.now(), window_label="1h"),
    )
    changes = run_autonomy_tick(ctrl, slo_engine=None, query=None, conn=tmp_db)
    assert changes == []
    assert ctrl.current_tier(AGENT, TASK) == AutonomyTier.T3


def test_run_autonomy_tick_never_promotes(tmp_db, frozen_clock):
    # Even with a totally clean snapshot, tick must not promote.
    ctrl, store, _ = _make_controller(tmp_db, frozen_clock, default_tier=AutonomyTier.T1)
    _insert_snapshot(
        tmp_db,
        _snapshot(burn_rate=0.1, ts=frozen_clock.now(), window_label="1h"),
    )
    for i in range(200):
        store.emit(
            run_id=f"r{i}", agent_id=AGENT, task_class=TASK, model_version=MODEL,
            step=0, event_type="judgment", attrs={"verdict": "pass"},
        )
    changes = run_autonomy_tick(ctrl, slo_engine=None, query=None, conn=tmp_db)
    assert changes == []
    assert ctrl.current_tier(AGENT, TASK) == AutonomyTier.T1
