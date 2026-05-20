"""EventQuery — read-only iterators over wide_events.

Used by the Judge worker (`judge/pipeline.py`) and the SLI module (`sli.py`).
All methods return iterators of typed WideEvent objects; never raw rows.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterator

from acp.schemas.wide_event import WideEvent, from_db_row


def _rows_to_events(rows: list[sqlite3.Row]) -> Iterator[WideEvent]:
    for r in rows:
        yield from_db_row({k: r[k] for k in r.keys()})


class EventQuery:
    """Thin read-only facade. No writes, no transactions."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- by run ------------------------------------------------------------

    def by_run(self, run_id: str) -> Iterator[WideEvent]:
        rows = self.conn.execute(
            "SELECT * FROM wide_events WHERE run_id = ? ORDER BY step ASC", (run_id,)
        ).fetchall()
        return _rows_to_events(rows)

    # -- by SLO key (agent × task_class × model_version × time) -----------

    def by_agent_class_window(
        self,
        agent_id: str,
        task_class: str,
        model_version: str,
        since_ms: int,
        until_ms: int,
    ) -> Iterator[WideEvent]:
        rows = self.conn.execute(
            "SELECT * FROM wide_events "
            "WHERE agent_id = ? AND task_class = ? AND model_version = ? "
            "AND ts >= ? AND ts < ? "
            "ORDER BY ts ASC",
            (agent_id, task_class, model_version, since_ms, until_ms),
        ).fetchall()
        return _rows_to_events(rows)

    def by_task_class_window(
        self,
        task_class: str,
        model_version: str,
        since_ms: int,
        until_ms: int,
    ) -> Iterator[WideEvent]:
        rows = self.conn.execute(
            "SELECT * FROM wide_events "
            "WHERE task_class = ? AND model_version = ? AND ts >= ? AND ts < ? "
            "ORDER BY ts ASC",
            (task_class, model_version, since_ms, until_ms),
        ).fetchall()
        return _rows_to_events(rows)

    def by_event_type(self, event_type: str, since_ms: int) -> Iterator[WideEvent]:
        rows = self.conn.execute(
            "SELECT * FROM wide_events WHERE event_type = ? AND ts >= ? ORDER BY ts ASC",
            (event_type, since_ms),
        ).fetchall()
        return _rows_to_events(rows)

    def count_where(self, **filters: Any) -> int:
        """Exact-match count with optional `since_ms` / `until_ms` time window.

        Recognized keys: agent_id, task_class, model_version, event_type, run_id,
        outcome, tool_name, since_ms, until_ms.
        """
        allowed = {
            "agent_id",
            "task_class",
            "model_version",
            "event_type",
            "run_id",
            "outcome",
            "tool_name",
        }
        clauses: list[str] = []
        params: list[Any] = []
        for k, v in filters.items():
            if k in allowed:
                clauses.append(f"{k} = ?")
                params.append(v)
            elif k == "since_ms":
                clauses.append("ts >= ?")
                params.append(v)
            elif k == "until_ms":
                clauses.append("ts < ?")
                params.append(v)
            else:
                raise ValueError(f"unknown filter: {k}")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT COUNT(*) FROM wide_events{where}"
        return int(self.conn.execute(sql, params).fetchone()[0])
