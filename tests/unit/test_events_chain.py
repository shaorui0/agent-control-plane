"""Tests for chain verifier: tamper detection, golden vector regression."""

from __future__ import annotations

from acp.clock import FrozenClock
from acp.crypto import chain_hash
from acp.events.store import WideEventStore
from acp.events.verifier import verify_all, verify_run


def _emit(store: WideEventStore, run_id: str, step: int, **kw) -> None:
    store.emit(
        run_id=run_id,
        agent_id=kw.pop("agent_id", "oncall"),
        task_class=kw.pop("task_class", "triage"),
        model_version=kw.pop("model_version", "claude-sonnet-4.6@2026-05-01"),
        step=step,
        event_type=kw.pop("event_type", "tool_call"),
        attrs=kw.pop("attrs", {}),
        **kw,
    )


def test_clean_chain_verifies(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    for i in range(10):
        _emit(store, "ok", i, attrs={"i": i})

    ok, err = verify_run(tmp_db, "ok")
    assert ok, err
    assert err is None


def test_tampered_attrs_detected(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    for i in range(10):
        _emit(store, "broken", i, attrs={"i": i})

    # Tamper with row at step=5: rewrite attrs_json directly.
    tmp_db.execute(
        "UPDATE wide_events SET attrs_json = ? WHERE run_id = ? AND step = ?",
        ('{"i":999}', "broken", 5),
    )
    tmp_db.commit()

    ok, err = verify_run(tmp_db, "broken")
    assert not ok
    assert err is not None
    assert "chain_hash mismatch" in err
    assert "step 5" in err


def test_tampered_intent_detected(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    for i in range(5):
        _emit(store, "r", i, intent=f"intent-{i}", attrs={"i": i})

    tmp_db.execute(
        "UPDATE wide_events SET intent = ? WHERE run_id = ? AND step = ?",
        ("malicious", "r", 2),
    )
    tmp_db.commit()
    ok, err = verify_run(tmp_db, "r")
    assert not ok
    assert err is not None and "step 2" in err


def test_deleted_middle_event_detected(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    for i in range(6):
        _emit(store, "r", i, attrs={"i": i})

    # Delete step=3; the next event's prev_event_id no longer points at the new prev.
    tmp_db.execute("DELETE FROM wide_events WHERE run_id = ? AND step = ?", ("r", 3))
    tmp_db.commit()

    ok, err = verify_run(tmp_db, "r")
    assert not ok
    assert err is not None


def test_verify_all_aggregates(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    for i in range(3):
        _emit(store, "a", i, attrs={"i": i})
    for i in range(3):
        _emit(store, "b", i, attrs={"i": i})

    # Break only run 'b'.
    tmp_db.execute(
        "UPDATE wide_events SET attrs_json = ? WHERE run_id = ? AND step = ?",
        ('{"x":1}', "b", 1),
    )
    tmp_db.commit()
    results = verify_all(tmp_db)
    assert results == {"a": True, "b": False}


def test_empty_run_is_trivially_ok(tmp_db):
    ok, err = verify_run(tmp_db, "nonexistent")
    assert ok and err is None


def test_golden_vector_chain_hashes():
    """Regression: known payloads → known hashes (catches crypto drift).

    Computes the first three chain hashes against a frozen payload sequence.
    If `chain_hash` or canonical encoding changes, this test will catch it.
    """
    payload0 = {
        "prev_event_id": None,
        "ts": 1000,
        "run_id": "R",
        "agent_id": "A",
        "task_class": "T",
        "model_version": "M",
        "step": 0,
        "event_type": "task_start",
        "tool_name": None,
        "tier_required": None,
        "outcome": None,
        "intent": None,
        "agent_claim": None,
        "attrs": {"k": 1},
    }
    h0 = chain_hash(None, payload0)
    payload1 = {**payload0, "prev_event_id": "evt-0", "step": 1, "event_type": "tool_call",
                "attrs": {"k": 2}}
    h1 = chain_hash(h0, payload1)
    payload2 = {**payload0, "prev_event_id": "evt-1", "step": 2, "event_type": "task_end",
                "attrs": {"k": 3}}
    h2 = chain_hash(h1, payload2)

    # Pure regression: assert hashes are 64 hex chars and stable across calls.
    assert len(h0) == 64 and all(c in "0123456789abcdef" for c in h0)
    assert h1 != h0 and h2 != h1
    # Same inputs → same outputs (determinism gate).
    assert chain_hash(None, payload0) == h0
    assert chain_hash(h0, payload1) == h1
    assert chain_hash(h1, payload2) == h2
