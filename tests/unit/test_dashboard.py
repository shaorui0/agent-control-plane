"""Dashboard HTML render tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acp.autonomy.controller import AutonomyController
from acp.events.store import WideEventStore
from acp.human.approval import ApprovalQueue
from acp.human.audit import AuditQueue
from acp.human.dashboard import build_dashboard_router
from acp.registry.store import RegistryStore


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
def app_client(tmp_db, tmp_path: Path, frozen_clock):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "oncall.yaml").write_text(_AGENT_YAML)
    registry = RegistryStore(tmp_db, d)
    registry.load()
    events = WideEventStore(tmp_db, clock=frozen_clock)
    autonomy = AutonomyController(tmp_db, events, registry_store=registry, clock=frozen_clock)
    autonomy.initialize_for_agent("oncall-agent")
    approvals = ApprovalQueue(tmp_db, event_store=events, clock=frozen_clock)
    audit = AuditQueue(tmp_db, clock=frozen_clock)

    app = FastAPI()
    app.include_router(
        build_dashboard_router(tmp_db, registry, autonomy, approvals, audit, refresh_seconds=30)
    )
    return TestClient(app)


def test_dashboard_renders_html(app_client):
    r = app_client.get("/dashboard")
    assert r.status_code == 200
    body = r.text
    assert "<html" in body.lower()
    assert "oncall-agent" in body
    # Tier visible.
    assert "T1" in body
    # Auto-refresh meta tag present.
    assert 'http-equiv="refresh"' in body
    assert 'content="30"' in body


def test_dashboard_shows_queue_counts(app_client, tmp_db):
    # Seed pending approval + audit.
    tmp_db.execute(
        "INSERT INTO approvals (approval_id, event_id, agent_id, tool_name, intent, args_json, status) "
        "VALUES ('APP1', 'RUN1', 'oncall-agent', 'kubectl_scale', 'go', '{}', 'pending')"
    )
    tmp_db.execute(
        "INSERT INTO audit_queue (audit_id, event_id, reason, status) "
        "VALUES ('AUD1', 'EV1', 'sample', 'pending')"
    )
    tmp_db.commit()
    r = app_client.get("/dashboard")
    assert "Approvals pending" in r.text
    assert "Audits pending" in r.text
