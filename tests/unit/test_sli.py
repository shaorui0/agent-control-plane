"""Tests for SLI helpers."""

from __future__ import annotations

from acp.clock import FrozenClock
from acp.events.query import EventQuery
from acp.events.sli import (
    intervention_free_rate,
    judge_pass_rate,
    p95_latency_ms,
    silent_fail_rate,
)
from acp.events.store import WideEventStore


AGENT = "oncall"
TASK = "triage"
MODEL = "claude-sonnet-4.6@2026-05-01"


def _emit_judgment(store: WideEventStore, run_id: str, step: int, verdict: str, **attrs):
    store.emit(
        run_id=run_id, agent_id=AGENT, task_class=TASK, model_version=MODEL,
        step=step, event_type="judgment",
        attrs={"verdict": verdict, **attrs},
    )


def _emit_task_end(store: WideEventStore, run_id: str, step: int, duration_ms: float):
    store.emit(
        run_id=run_id, agent_id=AGENT, task_class=TASK, model_version=MODEL,
        step=step, event_type="task_end",
        attrs={"duration_ms": duration_ms},
    )


def test_judge_pass_rate_basic(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    for i in range(100):
        _emit_judgment(store, f"r{i}", 0, "pass" if i < 80 else "fail")

    rate = judge_pass_rate(
        EventQuery(tmp_db), AGENT, TASK, MODEL, window_seconds=3600, clock=frozen_clock
    )
    assert rate == 0.8


def test_judge_pass_rate_empty_window_zero(tmp_db, frozen_clock):
    rate = judge_pass_rate(
        EventQuery(tmp_db), AGENT, TASK, MODEL, window_seconds=3600, clock=frozen_clock
    )
    assert rate == 0.0


def test_judge_pass_rate_excludes_outside_window(tmp_db):
    clk = FrozenClock(at_ms=10_000_000)
    store = WideEventStore(tmp_db, clock=clk)

    # 5 passes well inside the window (within last 60s).
    for i in range(5):
        _emit_judgment(store, f"r-in-{i}", 0, "pass")

    # Now jump clock far forward and emit 5 fails — these are "now" but old ones are stale.
    clk.advance(3_600_000)  # +1h
    for i in range(5):
        _emit_judgment(store, f"r-new-{i}", 0, "fail")

    # Window=60s should only see the 5 recent fails → pass rate 0.0.
    rate = judge_pass_rate(
        EventQuery(tmp_db), AGENT, TASK, MODEL, window_seconds=60, clock=clk
    )
    assert rate == 0.0

    # Wide window (2h) should see all 10 → pass rate 0.5.
    rate2 = judge_pass_rate(
        EventQuery(tmp_db), AGENT, TASK, MODEL, window_seconds=7200, clock=clk
    )
    assert rate2 == 0.5


def test_intervention_free_rate(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    # 10 runs end normally; 2 of them are also intervened.
    for i in range(10):
        store.emit(
            run_id=f"run-{i}", agent_id=AGENT, task_class=TASK, model_version=MODEL,
            step=0, event_type="task_start",
        )
        if i < 2:
            store.emit(
                run_id=f"run-{i}", agent_id=AGENT, task_class=TASK, model_version=MODEL,
                step=1, event_type="intervention", attrs={"reason": "burn"},
            )
        store.emit(
            run_id=f"run-{i}", agent_id=AGENT, task_class=TASK, model_version=MODEL,
            step=2, event_type="task_end", attrs={"duration_ms": 100.0},
        )

    rate = intervention_free_rate(
        EventQuery(tmp_db), AGENT, TASK, MODEL, window_seconds=3600, clock=frozen_clock
    )
    assert rate == 0.8


def test_silent_fail_rate(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    # 10 judgments: 8 pass cleanly, 2 originally passed but retroactively flipped.
    for i in range(8):
        _emit_judgment(store, f"clean-{i}", 0, "pass")
    for i in range(2):
        _emit_judgment(
            store,
            f"flipped-{i}",
            0,
            "fail",
            retroactively_flipped=1,
            original_verdict="pass",
        )

    rate = silent_fail_rate(
        EventQuery(tmp_db), AGENT, TASK, MODEL, window_seconds=3600, clock=frozen_clock
    )
    # 2 silent fails / 10 originally-passing = 0.2
    assert rate == 0.2


def test_silent_fail_rate_zero_when_no_passes(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    for i in range(5):
        _emit_judgment(store, f"r{i}", 0, "fail")
    rate = silent_fail_rate(
        EventQuery(tmp_db), AGENT, TASK, MODEL, window_seconds=3600, clock=frozen_clock
    )
    assert rate == 0.0


def test_p95_latency(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    # Durations 1..100 ms; nearest-rank p95 → index 94 → value 95.0.
    for i in range(1, 101):
        _emit_task_end(store, f"r{i}", 0, float(i))

    p95 = p95_latency_ms(
        EventQuery(tmp_db), AGENT, TASK, MODEL, window_seconds=3600, clock=frozen_clock
    )
    assert 90.0 <= p95 <= 100.0
    assert p95 == 95.0


def test_p95_empty_returns_zero(tmp_db, frozen_clock):
    p95 = p95_latency_ms(
        EventQuery(tmp_db), AGENT, TASK, MODEL, window_seconds=3600, clock=frozen_clock
    )
    assert p95 == 0.0


def test_agent_filter_isolates_metrics(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    # Two agents share a task_class; SLI must isolate per-agent.
    for i in range(10):
        store.emit(
            run_id=f"A-{i}", agent_id="agentA", task_class=TASK, model_version=MODEL,
            step=0, event_type="judgment", attrs={"verdict": "pass"},
        )
    for i in range(10):
        store.emit(
            run_id=f"B-{i}", agent_id="agentB", task_class=TASK, model_version=MODEL,
            step=0, event_type="judgment", attrs={"verdict": "fail"},
        )

    a_rate = judge_pass_rate(
        EventQuery(tmp_db), "agentA", TASK, MODEL, window_seconds=3600, clock=frozen_clock
    )
    b_rate = judge_pass_rate(
        EventQuery(tmp_db), "agentB", TASK, MODEL, window_seconds=3600, clock=frozen_clock
    )
    all_rate = judge_pass_rate(
        EventQuery(tmp_db), None, TASK, MODEL, window_seconds=3600, clock=frozen_clock
    )
    assert a_rate == 1.0
    assert b_rate == 0.0
    assert all_rate == 0.5
