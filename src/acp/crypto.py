"""Event chain integrity via blake2b over canonical CBOR (or JSON fallback)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

try:
    import cbor2  # type: ignore[import-not-found]
    _HAS_CBOR = True
except ImportError:  # pragma: no cover
    cbor2 = None  # type: ignore[assignment]
    _HAS_CBOR = False


def _canonical(payload: dict) -> bytes:
    """Deterministic byte serialization.

    Prefers CBOR canonical encoding; falls back to sorted JSON if cbor2 missing.
    """
    if _HAS_CBOR:
        return cbor2.dumps(payload, canonical=True)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def chain_hash(prev_hash: str | None, payload: dict) -> str:
    """blake2b(prev_hash_bytes || canonical(payload)) → 64-hex digest."""
    h = hashlib.blake2b(digest_size=32)
    if prev_hash:
        h.update(prev_hash.encode("ascii"))
    h.update(_canonical(payload))
    return h.hexdigest()


def verify_chain(events: list[dict[str, Any]]) -> bool:
    """Walk the chain; return False on any mismatch.

    Each event must have keys: prev_event_id (None or str), payload (dict),
    chain_hash (str). The first event's prev_hash is None.
    """
    prev: str | None = None
    for ev in events:
        expected = chain_hash(prev, ev["payload"])
        if expected != ev.get("chain_hash"):
            return False
        prev = ev["chain_hash"]
    return True
