"""ACP wide-event store package.

Public API:
  - WideEventStore: append-only, blake2b-chained event log.
  - EventQuery: read-only iterators over wide_events.
  - verify_run / verify_all: offline chain integrity checks.
  - SLI helpers: judge_pass_rate / intervention_free_rate /
                 silent_fail_rate / p95_latency_ms.
"""

from acp.events.query import EventQuery
from acp.events.sli import (
    intervention_free_rate,
    judge_pass_rate,
    p95_latency_ms,
    silent_fail_rate,
)
from acp.events.store import WideEventStore
from acp.events.verifier import verify_all, verify_run

__all__ = [
    "WideEventStore",
    "EventQuery",
    "verify_run",
    "verify_all",
    "judge_pass_rate",
    "intervention_free_rate",
    "silent_fail_rate",
    "p95_latency_ms",
]
