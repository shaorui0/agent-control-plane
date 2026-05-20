"""Offline chain integrity verifier.

Walks every event of a run in step order and recomputes blake2b chain hashes.
Detects: row tamper (attrs/intent/agent_claim mutation), reordering, deletion,
prev pointer rewrite.

CLI: `python -m acp.events.verifier --db <path>` → exit 0 if good, 1 if break.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Any

from acp.crypto import chain_hash
from acp.db import connect


def _row_to_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Rebuild the payload that was hashed at emit time.

    Mirrors `store._chain_payload`.
    """
    attrs_json = row["attrs_json"]
    attrs = json.loads(attrs_json) if attrs_json else {}
    return {
        "prev_event_id": row["prev_event_id"],
        "ts": row["ts"],
        "run_id": row["run_id"],
        "agent_id": row["agent_id"],
        "task_class": row["task_class"],
        "model_version": row["model_version"],
        "step": row["step"],
        "event_type": row["event_type"],
        "tool_name": row["tool_name"],
        "tier_required": row["tier_required"],
        "outcome": row["outcome"],
        "intent": row["intent"],
        "agent_claim": row["agent_claim"],
        "attrs": attrs,
    }


def verify_run(conn: sqlite3.Connection, run_id: str) -> tuple[bool, str | None]:
    """Verify the chain for one run. Returns (ok, error_message)."""
    rows = conn.execute(
        "SELECT * FROM wide_events WHERE run_id = ? ORDER BY step ASC", (run_id,)
    ).fetchall()
    if not rows:
        return True, None

    prev_hash: str | None = None
    prev_event_id: str | None = None
    last_step = -1
    for idx, row in enumerate(rows):
        # Step monotonicity check.
        if row["step"] <= last_step:
            return False, (
                f"step regression at idx {idx} (run {run_id}): "
                f"step {row['step']} <= last {last_step}"
            )
        last_step = row["step"]

        # prev_event_id linkage must match the actual previous row.
        if row["prev_event_id"] != prev_event_id:
            return False, (
                f"prev_event_id mismatch at idx {idx} (run {run_id}, step {row['step']}): "
                f"row says {row['prev_event_id']!r}, expected {prev_event_id!r}"
            )

        # Recompute chain hash.
        expected = chain_hash(prev_hash, _row_to_payload(row))
        if expected != row["chain_hash"]:
            return False, (
                f"chain_hash mismatch at idx {idx} (run {run_id}, "
                f"event {row['event_id']}, step {row['step']}): "
                f"expected {expected}, stored {row['chain_hash']}"
            )

        prev_hash = row["chain_hash"]
        prev_event_id = row["event_id"]

    return True, None


def verify_all(conn: sqlite3.Connection) -> dict[str, bool]:
    """Verify every distinct run_id in the table. Returns {run_id: ok}."""
    run_ids = [
        r["run_id"]
        for r in conn.execute("SELECT DISTINCT run_id FROM wide_events").fetchall()
    ]
    results: dict[str, bool] = {}
    for rid in run_ids:
        ok, _ = verify_run(conn, rid)
        results[rid] = ok
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify ACP wide-event chain integrity.")
    parser.add_argument("--db", required=True, help="Path to SQLite DB.")
    parser.add_argument(
        "--run-id", default=None, help="Verify only one run; default: all runs."
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress per-run output.")
    args = parser.parse_args(argv)

    conn = connect(args.db)
    try:
        if args.run_id:
            ok, err = verify_run(conn, args.run_id)
            if not args.quiet:
                status = "OK" if ok else "BREAK"
                print(f"{status} run {args.run_id}" + (f": {err}" if err else ""))
            return 0 if ok else 1

        results = verify_all(conn)
        if not results:
            if not args.quiet:
                print("no runs to verify")
            return 0

        broken = [rid for rid, ok in results.items() if not ok]
        if not args.quiet:
            for rid, ok in results.items():
                print(f"{'OK   ' if ok else 'BREAK'} {rid}")
            print(f"\n{len(results)} run(s), {len(broken)} broken.")
        return 0 if not broken else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
