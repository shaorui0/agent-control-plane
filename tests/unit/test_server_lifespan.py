"""Server lifespan tests: create_app runs migrations, loads registry, mounts routers."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.server import create_app
from acp.settings import Settings


_AGENT_YAML = """
agent_id: oncall-agent
owner: rshao@datavisor.com
version: 1.0.0
model_version: claude-sonnet-4-7
description: test
task_classes:
  - {name: triage, slo_sli_kind: judge_pass_rate, slo_target: 0.8, slo_window: 7d}
sealed_tools:
  - {name: vm_query, max_tier: T1, requires_intent: false}
budget_hourly_usd: 5.0
budget_hourly_tokens: 100000
default_tier: T1
"""


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    d = tmp_path / "agents"
    d.mkdir()
    (d / "oncall.yaml").write_text(_AGENT_YAML)
    return Settings(
        db_path=tmp_path / "acp.db",
        registry_dir=d,
        port=8080,
        dashboard_refresh_seconds=10,
    )


def test_create_app_lifespan_runs_startup_and_shutdown(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        # Startup ran -> registry table exists, agent loaded.
        state = app.state.acp
        assert state.registry.get("oncall-agent") is not None
        # Migrations ran -> wide_events table exists.
        n = state.conn.execute("SELECT COUNT(*) FROM wide_events").fetchone()[0]
        assert n == 0

        # Endpoints mounted.
        r = client.get("/healthz")
        assert r.status_code == 200
        r = client.get("/readyz")
        assert r.status_code == 200
        r = client.get("/dashboard")
        assert r.status_code == 200
        assert "oncall-agent" in r.text
        # Approval + audit routes mounted.
        r = client.get("/v1/approvals")
        assert r.status_code == 200
        r = client.get("/v1/audit")
        assert r.status_code == 200
        # Gateway router mounted at /v1.
        r = client.post(
            "/v1/sessions",
            json={"agent_id": "oncall-agent", "task_class": "triage", "input": {}},
        )
        assert r.status_code == 200, r.text


def test_autonomy_controller_injected_into_gateway(settings):
    """The gateway must use the real AutonomyController, not DefaultAutonomyProvider."""
    app = create_app(settings)
    with TestClient(app):
        from acp.autonomy.controller import AutonomyController

        state = app.state.acp
        assert isinstance(state.autonomy, AutonomyController)


def test_autonomy_state_initialized_for_agent(settings):
    app = create_app(settings)
    with TestClient(app):
        state = app.state.acp
        row = state.conn.execute(
            "SELECT current_tier FROM autonomy_state WHERE agent_id = ? AND task_class = ?",
            ("oncall-agent", "triage"),
        ).fetchone()
        assert row is not None
        assert row["current_tier"] == "T1"
