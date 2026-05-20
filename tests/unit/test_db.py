"""DB migration + transaction tests."""
from __future__ import annotations

import sqlite3

import pytest

from acp.db import connect, migrate, transaction

# `agents` is intentionally NOT in this set — it is owned by RegistryStore
# (see acp/registry/store.py), not the SQL migration. This is the single
# source of truth fix from W5A. See evidence/wave5a_complete.md.
EXPECTED_TABLES = {
    "wide_events",
    "budgets",
    "judgments",
    "goodhart_flags",
    "outcome_signals",
    "slo_snapshots",
    "autonomy_state",
    "approvals",
    "audit_queue",
    "calibration",
    "action_tokens",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def test_migrate_creates_all_tables(tmp_db):
    assert EXPECTED_TABLES.issubset(_tables(tmp_db))


def test_wal_mode_and_foreign_keys(tmp_db):
    assert tmp_db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert tmp_db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_transaction_commits(tmp_db):
    with transaction(tmp_db):
        tmp_db.execute(
            "INSERT INTO budgets(agent_id, window_start, tokens, usd_micros, tool_calls) "
            "VALUES (?, ?, ?, ?, ?)",
            ("a1", 0, 0, 0, 0),
        )
    n = tmp_db.execute("SELECT COUNT(*) FROM budgets").fetchone()[0]
    assert n == 1


def test_transaction_rolls_back(tmp_db):
    with pytest.raises(RuntimeError):
        with transaction(tmp_db):
            tmp_db.execute(
                "INSERT INTO budgets(agent_id, window_start, tokens, usd_micros, tool_calls) "
                "VALUES (?, ?, ?, ?, ?)",
                ("a2", 0, 0, 0, 0),
            )
            raise RuntimeError("boom")
    n = tmp_db.execute("SELECT COUNT(*) FROM budgets WHERE agent_id='a2'").fetchone()[0]
    assert n == 0


def test_migrate_idempotent(tmp_db_path):
    conn = connect(tmp_db_path)
    migrate(conn)
    migrate(conn)  # second run should not error (IF NOT EXISTS)
    assert EXPECTED_TABLES.issubset(_tables(conn))
    conn.close()
