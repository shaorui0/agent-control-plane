"""Idempotency keys — T12 mitigation.

ALL idempotency keys are server-issued ULIDs. Agent-supplied keys (any string
the gateway didn't hand out for THIS run) are rejected. This blocks the class
of attack where an agent reuses a known key to mask a duplicate side-effect.

Storage is per-run, in-memory. Keys are consumed exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from acp.errors import DenyClosed
from acp.ids import new_ulid


@dataclass
class IdempotencyVault:
    """Per-process registry of issued, not-yet-consumed keys.

    Keyed by run_id → set of unconsumed ULIDs.
    """

    _issued: dict[str, set[str]] = field(default_factory=dict)

    def issue(self, run_id: str) -> str:
        """Mint a fresh ULID for this run; return it to the agent."""
        key = new_ulid()
        self._issued.setdefault(run_id, set()).add(key)
        return key

    def check_and_consume(self, key: str, run_id: str) -> None:
        """Consume an agent-presented key for this run.

        Rejects (DenyClosed) on:
          - run_id unknown
          - key not in this run's issued set (agent forged or reused)
        On success, key is popped (single-use semantics).
        """
        if not isinstance(key, str) or len(key) != 26:
            raise DenyClosed("idempotency_invalid_key", "not a 26-char ULID")
        bucket = self._issued.get(run_id)
        if bucket is None:
            raise DenyClosed("idempotency_no_session", "run_id has no issued keys")
        if key not in bucket:
            raise DenyClosed("idempotency_unknown_key", "key not issued by server for this run")
        bucket.discard(key)

    def reset_run(self, run_id: str) -> None:
        """Clear all unconsumed keys for a run (called on session end)."""
        self._issued.pop(run_id, None)


def issue_idempotency_key(vault: IdempotencyVault, run_id: str) -> str:
    """Module-level convenience wrapper around `IdempotencyVault.issue`."""
    return vault.issue(run_id)
