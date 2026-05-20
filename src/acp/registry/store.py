"""RegistryStore: in-memory cache + SQLite mirror of loaded AgentSpecs.

Hot path is the in-memory dict. SQLite mirror exists so other modules (gateway,
SLO engine) can read agent metadata without sharing a Python object — it also
gives operators a queryable surface from the same DB file.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml

from acp.registry.loader import load_dir
from acp.registry.models import AgentSpec, ToolBinding

_DDL = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id           TEXT PRIMARY KEY,
    owner              TEXT NOT NULL,
    version            TEXT NOT NULL,
    model_version      TEXT NOT NULL,
    default_tier       TEXT NOT NULL,
    budget_hourly_usd  REAL NOT NULL,
    budget_hourly_tok  INTEGER NOT NULL,
    spec_yaml          TEXT NOT NULL,
    spec_json          TEXT NOT NULL,
    loaded_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class RegistryStore:
    """In-memory + SQLite-backed registry.

    Usage:
        store = RegistryStore(conn, Path("agents/"))
        store.load()
        spec = store.get("oncall-triage-agent")
        binding = store.get_tool_binding(spec.agent_id, "kubectl_scale")
    """

    def __init__(self, conn: sqlite3.Connection, dir: Path) -> None:
        self._conn = conn
        self._dir = dir
        self._agents: dict[str, AgentSpec] = {}
        self._tool_index: dict[tuple[str, str], ToolBinding] = {}
        self._conn.executescript(_DDL)
        self._conn.commit()

    # ---- public API -------------------------------------------------

    def load(self) -> None:
        """Load registry from disk into memory + mirror to SQLite (UPSERT)."""
        agents = load_dir(self._dir)
        self._agents = dict(agents)
        self._rebuild_tool_index()
        self._mirror_to_sqlite()

    def reload(self) -> None:
        """Clear cache and re-load. Suitable for SIGHUP."""
        self._agents.clear()
        self._tool_index.clear()
        self.load()

    def get(self, agent_id: str) -> AgentSpec | None:
        return self._agents.get(agent_id)

    def get_tool_binding(self, agent_id: str, tool_name: str) -> ToolBinding | None:
        return self._tool_index.get((agent_id, tool_name))

    def all_agents(self) -> list[AgentSpec]:
        return list(self._agents.values())

    # ---- internals --------------------------------------------------

    def _rebuild_tool_index(self) -> None:
        idx: dict[tuple[str, str], ToolBinding] = {}
        for spec in self._agents.values():
            for tool in spec.sealed_tools:
                idx[(spec.agent_id, tool.name)] = tool
        self._tool_index = idx

    def _mirror_to_sqlite(self) -> None:
        rows = []
        for spec in self._agents.values():
            rows.append(
                (
                    spec.agent_id,
                    spec.owner,
                    spec.version,
                    spec.model_version,
                    spec.default_tier.value,
                    spec.budget_hourly_usd,
                    spec.budget_hourly_tokens,
                    yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=True),
                    json.dumps(spec.model_dump(mode="json"), sort_keys=True),
                )
            )
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO agents (
                    agent_id, owner, version, model_version, default_tier,
                    budget_hourly_usd, budget_hourly_tok, spec_yaml, spec_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    owner=excluded.owner,
                    version=excluded.version,
                    model_version=excluded.model_version,
                    default_tier=excluded.default_tier,
                    budget_hourly_usd=excluded.budget_hourly_usd,
                    budget_hourly_tok=excluded.budget_hourly_tok,
                    spec_yaml=excluded.spec_yaml,
                    spec_json=excluded.spec_json,
                    loaded_at=datetime('now')
                """,
                rows,
            )
