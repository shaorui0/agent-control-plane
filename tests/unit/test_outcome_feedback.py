"""Tests for slo/feedback.py — the K2 retroactive verdict-flip defense."""

from __future__ import annotations

import json

from acp.clock import FrozenClock
from acp.events.store import WideEventStore
from acp.schemas.outcome import OutcomeSignal
from acp.slo.budget import split_events_by_budget_class
from acp.slo.feedback import (
    DEFAULT_SILENT_DELAY_S,
    ingest_outcome_signal,
    maybe_flip_verdict,
    silent_failure_detector,
)


AGENT = "oncall"
TASK = "triage"
MODEL = "claude-sonnet-4-7"


# -- helpers --------------------------------------------------------------


def _create_pass_judgment(
    conn,
    store: WideEventStore,
    run_id: str,
    ts: int,
    judgment_id: str = "j1",
) -> str:
    """Emit a task_end + judgment row. Returns judgment_id."""
    event = store.emit(
        run_id=run_id, agent_id=AGENT, task_class=TASK, model_version=MODEL,
        step=0, event_type="task_end",
        attrs={"final_output": "ok"}, ts=ts,
    )
    with conn:
        conn.execute(
            """
            INSERT INTO judgments
              (judgment_id, event_id, judge_name, judge_model, verdict,
               rubric_json, rationale, ts, retroactively_flipped)
            VALUES (?, ?, 'panel', 'gpt-4o', 'pass', ?, 'looks good', ?, 0)
            """,
            (judgment_id, event.event_id, json.dumps({}), ts),
        )
    return judgment_id


# -- test cases -----------------------------------------------------------


def test_oncall_refire_within_24h_flips_pass_to_fail(tmp_db):
    clk = FrozenClock(at_ms=10_000_000)
    store = WideEventStore(tmp_db, clock=clk)
    _create_pass_judgment(tmp_db, store, "run-1", ts=clk.now())

    # Advance 1 hour and ingest oncall_refire signal.
    clk.advance(3600 * 1000)
    sig = OutcomeSignal(
        signal_id="s1",
        run_id="run-1",
        kind="oncall_refire",
        value_json={"alert": "cpu_high"},
        delay_seconds=3600,
        source="pagerduty",
        ts=clk.now(),
    )
    ingest_outcome_signal(sig, tmp_db)

    flipped = maybe_flip_verdict("run-1", tmp_db, clk)
    assert flipped is True

    row = tmp_db.execute(
        "SELECT verdict, retroactively_flipped, original_verdict FROM judgments WHERE judgment_id='j1'"
    ).fetchone()
    assert row["verdict"] == "fail"
    assert row["retroactively_flipped"] == 1
    assert row["original_verdict"] == "pass"

    # Audit trail: an outcome wide_event was emitted.
    outcome_events = tmp_db.execute(
        "SELECT * FROM wide_events WHERE run_id='run-1' AND event_type='outcome'"
    ).fetchall()
    assert len(outcome_events) == 1
    attrs = json.loads(outcome_events[0]["attrs_json"])
    assert attrs["retroactive_fail"] is True
    assert attrs["original_verdict"] == "pass"


def test_rollback_required_flips(tmp_db):
    clk = FrozenClock(at_ms=20_000_000)
    store = WideEventStore(tmp_db, clock=clk)
    _create_pass_judgment(tmp_db, store, "run-2", ts=clk.now())

    sig = OutcomeSignal(
        signal_id="s2", run_id="run-2", kind="rollback_required",
        value_json={"by": "human"}, delay_seconds=600, source="ops",
        ts=clk.now() + 600_000,
    )
    ingest_outcome_signal(sig, tmp_db)
    assert maybe_flip_verdict("run-2", tmp_db, clk) is True


def test_silent_failure_detector_flips_after_48h(tmp_db):
    clk = FrozenClock(at_ms=100_000_000)
    store = WideEventStore(tmp_db, clock=clk)
    _create_pass_judgment(tmp_db, store, "run-3", ts=clk.now())

    # No git_applied ever arrives. Advance 49h.
    clk.advance((DEFAULT_SILENT_DELAY_S + 3600) * 1000)
    flipped = silent_failure_detector(tmp_db, clk)
    assert "run-3" in flipped

    row = tmp_db.execute(
        "SELECT verdict, retroactively_flipped FROM judgments WHERE judgment_id='j1'"
    ).fetchone()
    assert row["verdict"] == "fail"
    assert row["retroactively_flipped"] == 1


def test_silent_failure_detector_does_not_flip_when_git_applied(tmp_db):
    clk = FrozenClock(at_ms=100_000_000)
    store = WideEventStore(tmp_db, clock=clk)
    _create_pass_judgment(tmp_db, store, "run-4", ts=clk.now())

    # git_applied arrives in time.
    sig = OutcomeSignal(
        signal_id="s4", run_id="run-4", kind="git_applied",
        value_json={"sha": "abc"}, delay_seconds=1800, source="github",
        ts=clk.now() + 1800_000,
    )
    ingest_outcome_signal(sig, tmp_db)

    clk.advance((DEFAULT_SILENT_DELAY_S + 3600) * 1000)
    flipped = silent_failure_detector(tmp_db, clk)
    assert "run-4" not in flipped


def test_double_flip_is_idempotent(tmp_db):
    clk = FrozenClock(at_ms=30_000_000)
    store = WideEventStore(tmp_db, clock=clk)
    _create_pass_judgment(tmp_db, store, "run-5", ts=clk.now())

    sig = OutcomeSignal(
        signal_id="s5", run_id="run-5", kind="rollback_required",
        value_json={}, delay_seconds=10, source="ops", ts=clk.now() + 10_000,
    )
    ingest_outcome_signal(sig, tmp_db)
    assert maybe_flip_verdict("run-5", tmp_db, clk) is True
    # Second call should be a no-op (already flipped).
    assert maybe_flip_verdict("run-5", tmp_db, clk) is False


def test_split_events_by_budget_class(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    e1 = store.emit(
        run_id="r1", agent_id=AGENT, task_class=TASK, model_version=MODEL,
        step=0, event_type="judgment", attrs={"verdict": "pass"},
    )
    e2 = store.emit(
        run_id="r2", agent_id=AGENT, task_class=TASK, model_version=MODEL,
        step=0, event_type="judgment", attrs={"verdict": "pass"},
    )
    # Flag e2 as Goodhart-suspect (adversarial).
    with tmp_db:
        tmp_db.execute(
            """
            INSERT INTO goodhart_flags (flag_id, event_id, signal, evidence_json, ts)
            VALUES ('f1', ?, 'length_anomaly', '{}', ?)
            """,
            (e2.event_id, frozen_clock.now()),
        )

    organic, adversarial = split_events_by_budget_class([e1, e2], tmp_db)
    assert [e.event_id for e in organic] == [e1.event_id]
    assert [e.event_id for e in adversarial] == [e2.event_id]
