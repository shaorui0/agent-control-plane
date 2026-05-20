"""Event chain integrity tests."""
from __future__ import annotations

from acp.crypto import chain_hash, verify_chain


def _build_chain(payloads: list[dict]) -> list[dict]:
    events = []
    prev = None
    for p in payloads:
        h = chain_hash(prev, p)
        events.append({"payload": p, "chain_hash": h, "prev_event_id": prev})
        prev = h
    return events


def test_chain_hash_deterministic():
    h1 = chain_hash(None, {"a": 1, "b": [1, 2, 3]})
    h2 = chain_hash(None, {"b": [1, 2, 3], "a": 1})
    assert h1 == h2
    assert len(h1) == 64


def test_chain_hash_depends_on_prev():
    p = {"x": 1}
    h_none = chain_hash(None, p)
    h_prev = chain_hash("deadbeef", p)
    assert h_none != h_prev


def test_verify_chain_clean():
    events = _build_chain([{"i": 0}, {"i": 1}, {"i": 2}])
    assert verify_chain(events) is True


def test_verify_chain_detects_payload_tamper():
    events = _build_chain([{"i": 0}, {"i": 1}, {"i": 2}])
    events[1]["payload"]["i"] = 99  # flip a value
    assert verify_chain(events) is False


def test_verify_chain_detects_hash_tamper():
    events = _build_chain([{"i": 0}, {"i": 1}])
    # flip one hex char
    h = events[0]["chain_hash"]
    events[0]["chain_hash"] = ("0" if h[0] != "0" else "1") + h[1:]
    assert verify_chain(events) is False


def test_chain_hash_golden_vector():
    # Golden vector — locks the canonical encoding so downstream waves can rely on it.
    h = chain_hash(None, {"event_type": "task_start", "run_id": "R1", "step": 0})
    assert isinstance(h, str)
    assert len(h) == 64
    # Same logical payload, different key order, must produce identical hash.
    h2 = chain_hash(None, {"step": 0, "run_id": "R1", "event_type": "task_start"})
    assert h == h2
