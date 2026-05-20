"""Budget tracking — organic vs adversarial carve-out (M2 defense).

Adversarial events are those flagged by `judge/goodhart.py` (the row lands in
the `goodhart_flags` table). They burn an *adversarial* budget so a
prompt-injected or rubric-gamed event cannot consume the agent's organic SLO
budget — the K3 / M2 defense.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

from acp.schemas.wide_event import WideEvent


def budget_remaining(sli_value: float, target: float, window_seconds: int) -> float:
    """Fraction of error budget remaining in the window. Clamped to [0, 1].

    `window_seconds` is part of the signature for forward-compat (operator
    dashboards want to show "budget remaining in this window"); the math itself
    is dimensionless because both SLI and target live in [0, 1].

        remaining = (sli_value - target) / (1 - target)
    """
    if target >= 1.0:
        return 0.0 if sli_value < 1.0 else 1.0
    if target < 0.0:
        raise ValueError("target must be in [0, 1)")
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")
    remaining = (sli_value - target) / (1.0 - target)
    return max(0.0, min(1.0, remaining))


def _flagged_event_ids(conn: sqlite3.Connection) -> set[str]:
    """Read the set of event_ids that have at least one row in goodhart_flags."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT event_id FROM goodhart_flags"
        ).fetchall()
    except sqlite3.OperationalError:
        # Table not yet present (e.g. tests that skip the goodhart wave).
        return set()
    return {r[0] for r in rows}


def split_events_by_budget_class(
    events: Iterable[WideEvent],
    conn: sqlite3.Connection,
) -> tuple[list[WideEvent], list[WideEvent]]:
    """Partition events into (organic, adversarial) lists.

    An event is "adversarial" if its event_id appears in `goodhart_flags`. The
    flags table is the read-side source of truth — passing it in keeps this
    function pure-ish (no module-level state).
    """
    flagged = _flagged_event_ids(conn)
    organic: list[WideEvent] = []
    adversarial: list[WideEvent] = []
    for ev in events:
        if ev.event_id in flagged:
            adversarial.append(ev)
        else:
            organic.append(ev)
    return organic, adversarial
