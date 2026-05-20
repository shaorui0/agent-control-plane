"""SQLite connection + migration helpers.

WAL mode + foreign_keys=ON; provides sync (`connect`) and async helpers.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

MIGRATIONS_DIR = Path(__file__).parent / "events" / "migrations"


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection in WAL mode with safe defaults."""
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_size_limit=67108864")  # 64 MiB
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply all SQL files in `events/migrations/` in lexical order."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for f in files:
        sql = f.read_text(encoding="utf-8")
        conn.executescript(sql)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit IMMEDIATE transaction; rolls back on exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


async def aiosqlite_connect(path: Path | str):  # pragma: no cover — helper for async paths
    """Async SQLite connection mirror of `connect`."""
    import aiosqlite

    conn = await aiosqlite.connect(str(path))
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA journal_size_limit=67108864")
    await conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = __import__("aiosqlite").Row
    return conn
