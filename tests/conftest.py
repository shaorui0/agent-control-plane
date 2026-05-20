"""Shared pytest fixtures for ACP tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from acp.clock import FrozenClock
from acp.db import connect, migrate


@pytest.fixture
def tmp_db(tmp_path: Path):
    """Fresh migrated SQLite DB per test."""
    db_path = tmp_path / "acp.db"
    conn = connect(db_path)
    migrate(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "acp.db"


@pytest.fixture
def frozen_clock() -> FrozenClock:
    # Fixed instant: 2026-01-01T00:00:00Z in ms.
    return FrozenClock(at_ms=1_767_225_600_000)


@pytest.fixture
def sample_registry(tmp_path: Path) -> Path:
    """Stub registry dir for downstream waves; intentionally empty in Wave 1."""
    d = tmp_path / "agents"
    d.mkdir()
    return d


@pytest.fixture
def in_proc_client():
    """Placeholder for the SDK LocalClient fixture; wired in a later wave."""
    return None
