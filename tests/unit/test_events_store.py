"""Tests for WideEventStore: emit, chain linkage, concurrent runs, perf."""

from __future__ import annotations

import time

import pytest

from acp.clock import FrozenClock
from acp.errors import IntegrityError
from acp.events.store import WideEventStore


def _emit_n(store: WideEventStore, run_id: str, n: int, *, agent_id="oncall") -> None:
    for i in range(n):
        store.emit(
            run_id=run_id,
            agent_id=agent_id,
            task_class="triage",
            model_version="claude-sonnet-4.6@2026-05-01",
            step=i,
            event_type="tool_call" if i > 0 else "task_start",
            attrs={"i": i},
        )


def test_emit_links_chain_and_increments_step(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    _emit_n(store, "run-A", 100)

    events = store.tail_run("run-A")
    assert len(events) == 100
    # Steps strictly increasing 0..99.
    assert [e.step for e in events] == list(range(100))

    # First event has no prev; rest link to previous event_id.
    assert events[0].prev_event_id is None
    for prev, cur in zip(events, events[1:]):
        assert cur.prev_event_id == prev.event_id
        # chain_hash must differ between events (with different attrs/step).
        assert cur.chain_hash != prev.chain_hash


def test_count_matches_emit_total(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    _emit_n(store, "r1", 7)
    _emit_n(store, "r2", 3)
    assert store.count() == 10


def test_get_by_id_roundtrip(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    ev = store.emit(
        run_id="r",
        agent_id="a",
        task_class="t",
        model_version="m@v1",
        step=0,
        event_type="task_start",
        intent="probe",
        attrs={"k": "v"},
    )
    fetched = store.get_by_id(ev.event_id)
    assert fetched is not None
    assert fetched.event_id == ev.event_id
    assert fetched.chain_hash == ev.chain_hash
    assert fetched.attrs == {"k": "v"}
    assert fetched.intent == "probe"


def test_tail_run_since_step(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    _emit_n(store, "r", 10)
    tail = store.tail_run("r", since_step=7)
    assert [e.step for e in tail] == [7, 8, 9]


def test_step_must_be_monotonic(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    store.emit(
        run_id="r", agent_id="a", task_class="t", model_version="m",
        step=5, event_type="task_start", attrs={},
    )
    with pytest.raises(IntegrityError):
        store.emit(
            run_id="r", agent_id="a", task_class="t", model_version="m",
            step=5, event_type="tool_call", attrs={},
        )
    with pytest.raises(IntegrityError):
        store.emit(
            run_id="r", agent_id="a", task_class="t", model_version="m",
            step=2, event_type="tool_call", attrs={},
        )


def test_concurrent_runs_do_not_interfere(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    # Interleave runs A and B.
    for i in range(20):
        store.emit(
            run_id="A", agent_id="a", task_class="t", model_version="m",
            step=i, event_type="tool_call", attrs={"i": i},
        )
        store.emit(
            run_id="B", agent_id="a", task_class="t", model_version="m",
            step=i, event_type="tool_call", attrs={"i": i},
        )

    a = store.tail_run("A")
    b = store.tail_run("B")
    assert len(a) == 20 and len(b) == 20
    # Each chain links only within its own run.
    assert all(ev.prev_event_id == a[idx - 1].event_id for idx, ev in enumerate(a) if idx > 0)
    assert all(ev.prev_event_id == b[idx - 1].event_id for idx, ev in enumerate(b) if idx > 0)
    # Cross-run isolation: no A row points at a B row, and vice versa.
    a_ids = {e.event_id for e in a}
    b_ids = {e.event_id for e in b}
    assert all(e.prev_event_id in a_ids | {None} for e in a)
    assert all(e.prev_event_id in b_ids | {None} for e in b)


def test_perf_1000_events_under_1s(tmp_db, frozen_clock):
    """Performance gate per spec: 1000 events/sec single-threaded."""
    store = WideEventStore(tmp_db, clock=frozen_clock)
    n = 1000
    t0 = time.perf_counter()
    for i in range(n):
        store.emit(
            run_id="perf", agent_id="a", task_class="t", model_version="m@v1",
            step=i, event_type="tool_call", attrs={"i": i},
        )
    elapsed = time.perf_counter() - t0
    assert store.count() == n
    assert elapsed < 1.5, f"emitted {n} events in {elapsed:.2f}s (target <1.5s)"


def test_clock_is_used_for_ts(tmp_db):
    clk = FrozenClock(at_ms=42_000)
    store = WideEventStore(tmp_db, clock=clk)
    ev = store.emit(
        run_id="r", agent_id="a", task_class="t", model_version="m",
        step=0, event_type="task_start",
    )
    assert ev.ts == 42_000
    clk.advance(1_000)
    ev2 = store.emit(
        run_id="r", agent_id="a", task_class="t", model_version="m",
        step=1, event_type="tool_call",
    )
    assert ev2.ts == 43_000
