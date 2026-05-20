"""ULID generation and id helpers — no external dependency."""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time

# Crockford Base32 alphabet (no I, L, O, U)
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_lock = threading.Lock()
_last_ms: int = 0
_last_rand: int = 0


def _encode_b32(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def new_ulid(ms: int | None = None) -> str:
    """Generate a 26-char Crockford base32 ULID.

    Monotonic within the same millisecond by incrementing the random tail.
    """
    global _last_ms, _last_rand
    if ms is None:
        ms = time.time_ns() // 1_000_000
    with _lock:
        if ms == _last_ms:
            _last_rand = (_last_rand + 1) & ((1 << 80) - 1)
        else:
            _last_rand = int.from_bytes(secrets.token_bytes(10), "big")
            _last_ms = ms
        rand = _last_rand
    ts_part = _encode_b32(ms & ((1 << 48) - 1), 10)
    rand_part = _encode_b32(rand, 16)
    return ts_part + rand_part


def new_run_id() -> str:
    """A run_id is a ULID; prefix-free alias for clarity."""
    return new_ulid()


def args_hash(d: dict) -> str:
    """sha256 hex of canonical JSON serialization of a dict."""
    payload = json.dumps(d, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
