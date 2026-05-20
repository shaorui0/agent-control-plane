"""Approval queue tests — list/decide + intervention wide_event emission."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acp.events.store import WideEventStore
from acp.human.approval import ApprovalQueue, build_approval_router
from acp.ids import new_ulid
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
def setup(tmp_db, tmp_path: Path, frozen_clock):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "oncall.yaml").write_text(_AGENT_YAML)
    registry = RegistryStore(tmp_db, d)
    registry.load()
    events = WideEventStore(tmp_db, clock=frozen_clock)

    # Seed: emit a task_start so the run has a known step + agent context.
    run_id = "RUN12345678901234567890123456"
    events.emit(
        run_id=run_id,
        agent_id="oncall-agent",
        task_class="triage",
        model_version="claude-sonnet-4-7",
        step=1,
        event_type="task_start",
        outcome="ok",
    )

    # Seed an approval row (as the gateway would).
    approval_id = new_ulid()
    tmp_db.execute(
        "INSERT INTO approvals (approval_id, event_id, agent_id, tool_name, intent, args_json, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
        (
            approval_id,
            run_id,  # gateway stores run_id here (see gateway/routes.py)
            "oncall-agent",
            "kubectl_scale",
            "scale payments for spike",
            json.dumps({"deployment": "payments", "replicas_delta": 1}),
        ),
    )
    tmp_db.commit()

    queue = ApprovalQueue(tmp_db, event_store=events, clock=frozen_clock)
    return tmp_db, events, queue, approval_id, run_id


def test_list_pending_filters_by_agent(setup):
    conn, _, queue, approval_id, _ = setup
    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0].approval_id == approval_id
    assert pending[0].tool_name == "kubectl_scale"

    # Wrong agent filter -> empty.
    assert queue.list_pending(agent_id="ghost") == []
    assert len(queue.list_pending(agent_id="oncall-agent")) == 1


def test_decide_approves_and_emits_intervention(setup):
    conn, events, queue, approval_id, run_id = setup
    updated = queue.decide(approval_id, "approved", "rshao@datavisor.com", "looks good")
    assert updated.status == "approved"
    assert updated.decided_by == "rshao@datavisor.com"

    # No longer pending.
    assert queue.list_pending() == []

    # An intervention wide_event was emitted with outcome=approved.
    rows = conn.execute(
        "SELECT event_type, outcome, attrs_json FROM wide_events "
        "WHERE run_id = ? AND event_type = 'intervention'",
        (run_id,),
    ).fetchall()
    assert len(rows) == 1
    # WideEvent.outcome is a closed Literal — we map approve->ok and stash
    # the verbatim decision in attrs.decision for downstream consumers.
    assert rows[0]["outcome"] == "ok"
    attrs = json.loads(rows[0]["attrs_json"])
    assert attrs["decision"] == "approved"
    assert attrs["reviewer"] == "rshao@datavisor.com"


def test_decide_rejected_emits_outcome_rejected(setup):
    conn, _, queue, approval_id, run_id = setup
    queue.decide(approval_id, "rejected", "alice@x", "too risky")
    rows = conn.execute(
        "SELECT outcome, attrs_json FROM wide_events "
        "WHERE run_id = ? AND event_type = 'intervention'",
        (run_id,),
    ).fetchall()
    assert rows[0]["outcome"] == "denied"
    assert json.loads(rows[0]["attrs_json"])["decision"] == "rejected"


def test_decide_invalid_decision_raises(setup):
    _, _, queue, approval_id, _ = setup
    with pytest.raises(ValueError):
        queue.decide(approval_id, "maybe", "alice@x")  # type: ignore[arg-type]


def test_decide_unknown_raises(setup):
    _, _, queue, _, _ = setup
    with pytest.raises(KeyError):
        queue.decide("ghost-id", "approved", "alice@x")


def test_decide_twice_raises(setup):
    _, _, queue, approval_id, _ = setup
    queue.decide(approval_id, "approved", "alice@x")
    with pytest.raises(ValueError):
        queue.decide(approval_id, "approved", "alice@x")


def test_http_route_list_and_decide(setup):
    _, _, queue, approval_id, _ = setup
    app = FastAPI()
    app.include_router(build_approval_router(queue))
    client = TestClient(app)

    r = client.get("/v1/approvals")
    assert r.status_code == 200
    items = r.json()
    assert any(i["approval_id"] == approval_id for i in items)

    r2 = client.post(
        f"/v1/approvals/{approval_id}/decide",
        json={"decision": "approved", "reviewer": "rshao@x", "notes": ""},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "approved"


def test_http_route_unknown_returns_404(setup):
    _, _, queue, _, _ = setup
    app = FastAPI()
    app.include_router(build_approval_router(queue))
    client = TestClient(app)
    r = client.post(
        "/v1/approvals/ghost/decide",
        json={"decision": "approved", "reviewer": "x"},
    )
    assert r.status_code == 404
