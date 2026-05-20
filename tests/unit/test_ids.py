"""ULID + args_hash invariants."""
from __future__ import annotations

from acp.ids import args_hash, new_ulid


def test_ulid_length_and_alphabet():
    u = new_ulid()
    assert len(u) == 26
    assert all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in u)


def test_ulid_uniqueness_10k():
    seen = {new_ulid() for _ in range(10_000)}
    assert len(seen) == 10_000


def test_ulid_monotonic_within_same_ms():
    ms = 1_700_000_000_000
    a = new_ulid(ms)
    b = new_ulid(ms)
    c = new_ulid(ms)
    assert a < b < c


def test_ulid_monotonic_across_calls_real_time():
    prev = new_ulid()
    for _ in range(500):
        cur = new_ulid()
        assert cur > prev
        prev = cur


def test_args_hash_canonical():
    a = args_hash({"b": 2, "a": 1})
    b = args_hash({"a": 1, "b": 2})
    assert a == b
    assert len(a) == 64


def test_args_hash_changes_on_value_change():
    assert args_hash({"x": 1}) != args_hash({"x": 2})
