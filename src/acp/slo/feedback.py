"""Outcome feedback — the K2 defense. Verdicts are mutable.

External outcome signals (git_applied, oncall_refire, rollback_required,
csat_proxy, cost_delta) flow into `outcome_signals`. After each ingest we
re-evaluate the run's judgment and flip pass → fail if the world contradicts
the judge. The flip is recorded *twice*:

  1. UPDATE judgments SET verdict='fail', retroactively_flipped=1,
     original_verdict='pass'.
  2. Emit a new wide_event of event_type='outcome' with outcome='retroactive_fail'
     so the audit trail shows the flip happened.

A scheduled `silent_failure_detector` sweeps for `pass` judgments that *should*
have produced a `git_applied` signal within the configured window but did not —
those flip via the same path (silent failure).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from acp.clock import Clock
from acp.events.store import WideEventStore
from acp.ids import new_ulid
from acp.schemas.outcome import OutcomeSignal


# Window for the K2 retroactive-flip rules.
DEFAULT_REFIRE_WINDOW_S = 24 * 3600
DEFAULT_SILENT_DELAY_S = 48 * 3600


def ingest_outcome_signal(signal: OutcomeSignal, conn: sqlite3.Connection) -> None:
    """Write an OutcomeSignal to `outcome_signals` (idempotent on signal_id)."""
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO outcome_signals
              (signal_id, run_id, kind, value_json, delay_seconds, source, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.signal_id,
                signal.run_id,
                signal.kind,
                json.dumps(signal.value_json, sort_keys=True, separators=(",", ":")),
                signal.delay_seconds,
                signal.source,
                signal.ts,
            ),
        )


# -- judgment lookup -------------------------------------------------------


def _judgment_for_run(
    conn: sqlite3.Connection, run_id: str
) -> dict[str, Any] | None:
    """Find the latest judgment row for a given run_id (by joining on event_id).

    A run's judgment is stored as a row in `judgments` whose `event_id` points
    at a `wide_events` row that has the same run_id.
    """
    row = conn.execute(
        """
        SELECT j.*
        FROM judgments j
        JOIN wide_events e ON j.event_id = e.event_id
        WHERE e.run_id = ?
        ORDER BY j.ts DESC
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _signals_for_run(
    conn: sqlite3.Connection, run_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM outcome_signals WHERE run_id = ? ORDER BY ts ASC",
        (run_id,),
    ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def _judgment_wide_event(
    conn: sqlite3.Connection, event_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM wide_events WHERE event_id = ?", (event_id,)
    ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


# -- the flip --------------------------------------------------------------


def _should_flip(
    judgment: dict[str, Any],
    signals: list[dict[str, Any]],
    now_ms: int,
    *,
    refire_window_s: int,
    silent_delay_s: int,
) -> tuple[bool, str]:
    """Decide whether a pass judgment should be retroactively flipped.

    Returns (flip?, reason). The reason is recorded on the new outcome event.
    """
    if judgment["verdict"] != "pass":
        return False, "verdict_not_pass"
    if judgment.get("retroactively_flipped"):
        return False, "already_flipped"

    judged_ts = int(judgment["ts"])
    refire_cutoff_ms = judged_ts + refire_window_s * 1000

    has_git_applied = False
    for s in signals:
        kind = s["kind"]
        s_ts = int(s["ts"])
        if kind == "oncall_refire" and s_ts <= refire_cutoff_ms:
            return True, "oncall_refire_within_24h"
        if kind == "rollback_required":
            return True, "rollback_required"
        if kind == "git_applied":
            has_git_applied = True

    # Silent failure: pass judgment but no git_applied within silent_delay_s.
    silent_cutoff_ms = judged_ts + silent_delay_s * 1000
    if now_ms >= silent_cutoff_ms and not has_git_applied:
        return True, "no_git_applied_within_silent_window"

    return False, "no_trigger"


def _apply_flip(
    conn: sqlite3.Connection,
    judgment: dict[str, Any],
    reason: str,
    clock: Clock,
) -> None:
    """Update the judgments row + emit a wide_event audit trail row."""
    now = clock.now()
    original_verdict = judgment["verdict"]

    with conn:
        # Add a column lazily if older DBs don't have it. (Idempotent.)
        try:
            conn.execute(
                "ALTER TABLE judgments ADD COLUMN original_verdict TEXT"
            )
        except sqlite3.OperationalError:
            pass  # column already exists

    with conn:
        conn.execute(
            """
            UPDATE judgments
               SET verdict='fail',
                   retroactively_flipped=1,
                   original_verdict=COALESCE(original_verdict, ?)
             WHERE judgment_id=?
            """,
            (original_verdict, judgment["judgment_id"]),
        )

    # Emit a parallel wide_event so the chain reflects the flip.
    judged_event = _judgment_wide_event(conn, judgment["event_id"])
    if judged_event is None:
        return

    store = WideEventStore(conn, clock=clock)
    # Pick next step monotonically increasing for the run.
    next_step_row = conn.execute(
        "SELECT MAX(step) FROM wide_events WHERE run_id = ?",
        (judged_event["run_id"],),
    ).fetchone()
    next_step = int((next_step_row[0] or 0)) + 1

    store.emit(
        run_id=judged_event["run_id"],
        agent_id=judged_event["agent_id"],
        task_class=judged_event["task_class"],
        model_version=judged_event["model_version"],
        step=next_step,
        event_type="outcome",
        outcome="error",
        intent=None,
        agent_claim=None,
        attrs={
            "retroactive_fail": True,
            "original_verdict": original_verdict,
            "judgment_id": judgment["judgment_id"],
            "reason": reason,
            "flipped_at_ms": now,
        },
        ts=now,
    )


def maybe_flip_verdict(
    run_id: str,
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    refire_window_s: int = DEFAULT_REFIRE_WINDOW_S,
    silent_delay_s: int = DEFAULT_SILENT_DELAY_S,
) -> bool:
    """Inspect the run's judgment + signals; flip if K2 rules say to.

    Returns True iff a flip happened.
    """
    judgment = _judgment_for_run(conn, run_id)
    if judgment is None:
        return False
    signals = _signals_for_run(conn, run_id)
    flip, reason = _should_flip(
        judgment,
        signals,
        clock.now(),
        refire_window_s=refire_window_s,
        silent_delay_s=silent_delay_s,
    )
    if not flip:
        return False
    _apply_flip(conn, judgment, reason, clock)
    return True


def silent_failure_detector(
    conn: sqlite3.Connection,
    clock: Clock,
    max_delay_seconds: int = DEFAULT_SILENT_DELAY_S,
) -> list[str]:
    """Scan all `pass` judgments without `git_applied` past `max_delay_seconds`.

    Returns the list of run_ids that were flipped.
    """
    cutoff_ms = clock.now() - max_delay_seconds * 1000
    rows = conn.execute(
        """
        SELECT j.judgment_id, j.ts, e.run_id
          FROM judgments j
          JOIN wide_events e ON j.event_id = e.event_id
         WHERE j.verdict='pass'
           AND COALESCE(j.retroactively_flipped, 0)=0
           AND j.ts <= ?
        """,
        (cutoff_ms,),
    ).fetchall()

    flipped: list[str] = []
    for row in rows:
        run_id = row["run_id"]
        if maybe_flip_verdict(
            run_id, conn, clock, silent_delay_s=max_delay_seconds
        ):
            flipped.append(run_id)
    return flipped


__all__ = [
    "DEFAULT_REFIRE_WINDOW_S",
    "DEFAULT_SILENT_DELAY_S",
    "ingest_outcome_signal",
    "maybe_flip_verdict",
    "silent_failure_detector",
]


# Compatibility: some callers may import new_ulid from here.
_ = new_ulid  # quiet "unused import"
