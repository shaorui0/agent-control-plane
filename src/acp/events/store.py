"""WideEventStore — append-only, chain-hashed event storage over SQLite.

Per MASTER_PLAN.md axiom 6: wide events are the storage primitive. SLI is a
query over these rows, never a pre-aggregated counter.

Concurrency model: synchronous, single-process. `BEGIN IMMEDIATE` acquires the
write lock for the chain-tail lookup + insert so concurrent emitters serialize
cleanly without SQLITE_BUSY storms.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from acp.clock import Clock, default_clock
from acp.crypto import chain_hash
from acp.errors import IntegrityError
from acp.ids import new_ulid
from acp.schemas.wide_event import WideEvent, from_db_row, to_db_row


_INSERT_SQL = """
INSERT INTO wide_events (
    event_id, prev_event_id, ts, run_id, agent_id, task_class, model_version,
    step, event_type, tool_name, tier_required, outcome, intent, agent_claim,
    attrs_json, chain_hash
) VALUES (
    :event_id, :prev_event_id, :ts, :run_id, :agent_id, :task_class, :model_version,
    :step, :event_type, :tool_name, :tier_required, :outcome, :intent, :agent_claim,
    :attrs_json, :chain_hash
)
"""


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _chain_payload(values: dict[str, Any]) -> dict[str, Any]:
    """Subset of an event used as the chain hash input.

    Excludes `chain_hash` itself (obviously) and `event_id` so that retroactively
    inserting the id does not change the hash; the chain is over content + position.
    """
    return {
        "prev_event_id": values.get("prev_event_id"),
        "ts": values["ts"],
        "run_id": values["run_id"],
        "agent_id": values["agent_id"],
        "task_class": values["task_class"],
        "model_version": values["model_version"],
        "step": values["step"],
        "event_type": values["event_type"],
        "tool_name": values.get("tool_name"),
        "tier_required": values.get("tier_required"),
        "outcome": values.get("outcome"),
        "intent": values.get("intent"),
        "agent_claim": values.get("agent_claim"),
        "attrs": values.get("attrs", {}),
    }


class WideEventStore:
    """Append-only event store with per-run blake2b chain.

    The store is *intentionally* small: emit, get_by_id, tail_run, count. All
    aggregation lives in `query.py` and `sli.py`.
    """

    def __init__(self, conn: sqlite3.Connection, clock: Clock | None = None) -> None:
        self.conn = conn
        self.clock = clock or default_clock()

    # -- write path --------------------------------------------------------

    def emit(
        self,
        *,
        run_id: str,
        agent_id: str,
        task_class: str,
        model_version: str,
        step: int,
        event_type: str,
        tool_name: str | None = None,
        tier_required: str | None = None,
        outcome: str | None = None,
        intent: str | None = None,
        agent_claim: str | None = None,
        attrs: dict[str, Any] | None = None,
        ts: int | None = None,
    ) -> WideEvent:
        """Append an event to the run's chain. Returns the persisted WideEvent.

        Steps:
          1. BEGIN IMMEDIATE (acquire write lock).
          2. Look up the tail event for this run (highest step) → prev pointer.
          3. Compute chain_hash over canonical payload.
          4. INSERT.
          5. COMMIT.
        """
        attrs = attrs or {}
        ts = ts if ts is not None else self.clock.now()
        event_id = new_ulid()

        cur = self.conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            prev = cur.execute(
                "SELECT event_id, step, chain_hash FROM wide_events "
                "WHERE run_id = ? ORDER BY step DESC LIMIT 1",
                (run_id,),
            ).fetchone()

            prev_event_id = prev["event_id"] if prev else None
            prev_hash = prev["chain_hash"] if prev else None

            if prev is not None and step <= prev["step"]:
                raise IntegrityError(
                    f"step {step} not monotonic for run {run_id}; last step was {prev['step']}"
                )

            values: dict[str, Any] = {
                "event_id": event_id,
                "prev_event_id": prev_event_id,
                "ts": ts,
                "run_id": run_id,
                "agent_id": agent_id,
                "task_class": task_class,
                "model_version": model_version,
                "step": step,
                "event_type": event_type,
                "tool_name": tool_name,
                "tier_required": tier_required,
                "outcome": outcome,
                "intent": intent,
                "agent_claim": agent_claim,
                "attrs": attrs,
            }
            ch = chain_hash(prev_hash, _chain_payload(values))

            event = WideEvent(
                event_id=event_id,
                prev_event_id=prev_event_id,
                ts=ts,
                run_id=run_id,
                agent_id=agent_id,
                task_class=task_class,
                model_version=model_version,
                step=step,
                event_type=event_type,  # type: ignore[arg-type]
                tool_name=tool_name,
                tier_required=tier_required,  # type: ignore[arg-type]
                outcome=outcome,  # type: ignore[arg-type]
                intent=intent,
                agent_claim=agent_claim,
                attrs=attrs,
                chain_hash=ch,
            )
            cur.execute(_INSERT_SQL, to_db_row(event))
            cur.execute("COMMIT")
            return event
        except Exception:
            cur.execute("ROLLBACK")
            raise

    # -- read path ---------------------------------------------------------

    def get_by_id(self, event_id: str) -> WideEvent | None:
        row = self.conn.execute(
            "SELECT * FROM wide_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        d = _row_to_dict(row)
        return from_db_row(d) if d else None

    def tail_run(self, run_id: str, since_step: int = 0) -> list[WideEvent]:
        rows = self.conn.execute(
            "SELECT * FROM wide_events WHERE run_id = ? AND step >= ? ORDER BY step ASC",
            (run_id, since_step),
        ).fetchall()
        return [from_db_row(_row_to_dict(r)) for r in rows]  # type: ignore[arg-type]

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM wide_events").fetchone()[0])
