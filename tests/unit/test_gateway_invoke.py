"""End-to-end gateway invoke tests via in-process FastAPI app."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acp.events.store import WideEventStore
from acp.gateway.auth import SessionAuth
from acp.gateway.budget import BudgetManager
from acp.gateway.idempotency import IdempotencyVault
from acp.gateway.routes import DefaultAutonomyProvider, GatewayDeps, build_router
from acp.gateway.tools import REGISTRY  # noqa: F401 — import side effect registers tools
from acp.registry.store import RegistryStore


_AGENT_YAML = """
agent_id: oncall-agent
owner: rshao@datavisor.com
version: 1.0.0
model_version: claude-sonnet-4-7
description: test
task_classes:
  - name: triage
    slo_sli_kind: judge_pass_rate
    slo_target: 0.8
    slo_window: 7d
sealed_tools:
  - name: vm_query
    max_tier: T1
    requires_intent: false
  - name: slack_post
    max_tier: T2
    requires_intent: false
  - name: kubectl_scale
    max_tier: T3
    requires_intent: true
    kwargs_constraints:
      max_replicas_delta: 2
budget_hourly_usd: 5.0
budget_hourly_tokens: 100000
default_tier: T2
"""


@pytest.fixture
def app_and_deps(tmp_db, tmp_path: Path, frozen_clock):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "oncall-agent.yaml").write_text(_AGENT_YAML)
    registry = RegistryStore(tmp_db, d)
    registry.load()

    events = WideEventStore(tmp_db, clock=frozen_clock)
    auth = SessionAuth()
    budget = BudgetManager(tmp_db, registry, clock=frozen_clock)
    idem = IdempotencyVault()

    from acp.gateway.tools.base import REGISTRY as TOOL_REG

    deps = GatewayDeps(
        conn=tmp_db,
        registry=registry,
        events=events,
        auth=auth,
        budget=budget,
        idempotency=idem,
        tools=TOOL_REG,
        autonomy=DefaultAutonomyProvider(registry),
    )

    app = FastAPI()
    app.include_router(build_router(deps))
    return app, deps


class _FixedTier:
    def __init__(self, tier):
        self._t = tier

    def current_tier(self, agent_id: str, task_class: str):
        return self._t


def _start_session(client: TestClient, agent_id="oncall-agent", task_class="triage"):
    resp = client.post(
        "/v1/sessions", json={"agent_id": agent_id, "task_class": task_class, "input": {}}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(bearer):
    return {"Authorization": f"Bearer {bearer}"}


def test_session_create_and_invoke_happy_path(app_and_deps):
    app, _ = app_and_deps
    client = TestClient(app)
    sess = _start_session(client)
    r = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/vm_query/invoke",
        headers=_auth(sess["bearer"]),
        json={"args": {"query": "rate(http_requests_total[5m])"}, "intent": "", "idempotency_key": sess["idempotency_key"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert "result" in body
    assert "next_idempotency_key" in body


def test_missing_bearer(app_and_deps):
    app, _ = app_and_deps
    client = TestClient(app)
    sess = _start_session(client)
    r = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/vm_query/invoke",
        json={"args": {}, "intent": "", "idempotency_key": sess["idempotency_key"]},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error_code"] == "missing_bearer"


def test_invalid_bearer(app_and_deps):
    app, _ = app_and_deps
    client = TestClient(app)
    sess = _start_session(client)
    r = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/vm_query/invoke",
        headers=_auth("not-a-real-bearer"),
        json={"args": {}, "intent": "", "idempotency_key": sess["idempotency_key"]},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error_code"] == "invalid_bearer"


def test_unsealed_tool(app_and_deps):
    app, _ = app_and_deps
    client = TestClient(app)
    sess = _start_session(client)
    r = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/kubectl_rollout/invoke",
        headers=_auth(sess["bearer"]),
        json={"args": {}, "intent": "restart the deployment now please", "idempotency_key": sess["idempotency_key"]},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error_code"] == "tool_not_sealed"


def test_tier_too_high_for_t3_tool(app_and_deps):
    # default_tier T2, but kubectl_scale binding max_tier=T3 -> tier_too_high
    app, _ = app_and_deps
    client = TestClient(app)
    sess = _start_session(client)
    r = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/kubectl_scale/invoke",
        headers=_auth(sess["bearer"]),
        json={
            "args": {"deployment": "p", "replicas_delta": 1},
            "intent": "scale payments to handle traffic spike now",
            "idempotency_key": sess["idempotency_key"],
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error_code"] == "tier_too_high"


def test_intent_missing_when_required(app_and_deps, tmp_db, tmp_path):
    # Promote to T3 (via injected provider) so tier passes; then test intent-missing.
    yaml = """
agent_id: scale-agent
owner: rshao@datavisor.com
version: 1.0.0
model_version: m1
task_classes:
  - {name: triage, slo_sli_kind: judge_pass_rate, slo_target: 0.8, slo_window: 7d}
sealed_tools:
  - name: kubectl_scale
    max_tier: T3
    requires_intent: true
    kwargs_constraints: {max_replicas_delta: 2}
budget_hourly_usd: 5.0
budget_hourly_tokens: 100000
default_tier: T2
"""
    from acp.schemas.agent import AutonomyTier
    app, deps = app_and_deps
    (deps.registry._dir / "scale-agent.yaml").write_text(yaml)
    deps.registry.reload()
    deps.autonomy = _FixedTier(AutonomyTier.T3)
    client = TestClient(app)
    sess = _start_session(client, agent_id="scale-agent")
    r = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/kubectl_scale/invoke",
        headers=_auth(sess["bearer"]),
        json={
            "args": {"deployment": "p", "replicas_delta": 1},
            "intent": "",
            "idempotency_key": sess["idempotency_key"],
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error_code"] == "intent_missing"


def test_budget_exhausted(app_and_deps):
    app, deps = app_and_deps
    # pre-fill the budget to near cap
    deps.budget.record_actual("oncall-agent", 99_900, 0)
    client = TestClient(app)
    sess = _start_session(client)
    r = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/vm_query/invoke",
        headers=_auth(sess["bearer"]),
        json={
            "args": {"query": "x"},
            "intent": "",
            "idempotency_key": sess["idempotency_key"],
            "est_tokens": 500,
        },
    )
    assert r.status_code == 429
    assert r.json()["detail"]["error_code"] == "budget_exhausted"


def test_dlp_violation(app_and_deps):
    app, _ = app_and_deps
    client = TestClient(app)
    sess = _start_session(client)
    r = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/slack_post/invoke",
        headers=_auth(sess["bearer"]),
        json={
            "args": {"channel": "#sre", "text": "psst AKIAIOSFODNN7EXAMPLE"},
            "intent": "",
            "idempotency_key": sess["idempotency_key"],
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error_code"] == "egress_dlp_violation"


def test_idempotency_replay_rejected(app_and_deps):
    app, _ = app_and_deps
    client = TestClient(app)
    sess = _start_session(client)
    # first call consumes the key
    r1 = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/vm_query/invoke",
        headers=_auth(sess["bearer"]),
        json={"args": {"query": "x"}, "intent": "", "idempotency_key": sess["idempotency_key"]},
    )
    assert r1.status_code == 200
    # replay with same key -> denied
    r2 = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/vm_query/invoke",
        headers=_auth(sess["bearer"]),
        json={"args": {"query": "x"}, "intent": "", "idempotency_key": sess["idempotency_key"]},
    )
    assert r2.status_code == 400
    assert r2.json()["detail"]["error_code"] == "idempotency_unknown_key"


def test_t3_tool_goes_to_approval_queue(app_and_deps, tmp_path):
    yaml = """
agent_id: scale-agent
owner: rshao@datavisor.com
version: 1.0.0
model_version: m1
task_classes:
  - {name: triage, slo_sli_kind: judge_pass_rate, slo_target: 0.8, slo_window: 7d}
sealed_tools:
  - name: kubectl_scale
    max_tier: T3
    requires_intent: true
    kwargs_constraints: {max_replicas_delta: 2}
budget_hourly_usd: 5.0
budget_hourly_tokens: 100000
default_tier: T2
"""
    from acp.schemas.agent import AutonomyTier
    app, deps = app_and_deps
    (deps.registry._dir / "scale-agent.yaml").write_text(yaml)
    deps.registry.reload()
    deps.autonomy = _FixedTier(AutonomyTier.T3)
    client = TestClient(app)
    sess = _start_session(client, agent_id="scale-agent")
    r = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/kubectl_scale/invoke",
        headers=_auth(sess["bearer"]),
        json={
            "args": {"deployment": "p", "replicas_delta": 1},
            "intent": "scale payments deployment for traffic spike",
            "idempotency_key": sess["idempotency_key"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending_approval"
    assert "approval_id" in body

    # Polling endpoint
    r2 = client.get(
        f"/v1/sessions/{sess['run_id']}/approvals/{body['approval_id']}",
        headers=_auth(sess["bearer"]),
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "pending"


def test_decision_and_end(app_and_deps):
    app, _ = app_and_deps
    client = TestClient(app)
    sess = _start_session(client)
    r = client.post(
        f"/v1/sessions/{sess['run_id']}/decisions",
        headers=_auth(sess["bearer"]),
        json={"intent": "checking cpu", "rationale": "alert fired"},
    )
    assert r.status_code == 200
    r2 = client.post(
        f"/v1/sessions/{sess['run_id']}/end",
        headers=_auth(sess["bearer"]),
        json={"final_output": {"summary": "done"}, "agent_claim_outcome": "ok"},
    )
    assert r2.status_code == 200


def test_kwargs_constraint_violation(app_and_deps, tmp_path):
    yaml = """
agent_id: scale-agent
owner: rshao@datavisor.com
version: 1.0.0
model_version: m1
task_classes:
  - {name: triage, slo_sli_kind: judge_pass_rate, slo_target: 0.8, slo_window: 7d}
sealed_tools:
  - name: kubectl_scale
    max_tier: T3
    requires_intent: true
    kwargs_constraints: {max_replicas_delta: 2}
budget_hourly_usd: 5.0
budget_hourly_tokens: 100000
default_tier: T2
"""
    from acp.schemas.agent import AutonomyTier
    app, deps = app_and_deps
    (deps.registry._dir / "scale-agent.yaml").write_text(yaml)
    deps.registry.reload()
    deps.autonomy = _FixedTier(AutonomyTier.T3)
    client = TestClient(app)
    sess = _start_session(client, agent_id="scale-agent")
    r = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/kubectl_scale/invoke",
        headers=_auth(sess["bearer"]),
        json={
            "args": {"deployment": "p", "replicas_delta": 99},
            "intent": "scale payments deployment for traffic spike",
            "idempotency_key": sess["idempotency_key"],
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error_code"] == "kwargs_constraint_violation"


def test_unknown_agent(app_and_deps):
    app, _ = app_and_deps
    client = TestClient(app)
    r = client.post(
        "/v1/sessions", json={"agent_id": "ghost", "task_class": "x", "input": {}}
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error_code"] == "agent_unknown"


def test_unknown_task_class(app_and_deps):
    app, _ = app_and_deps
    client = TestClient(app)
    r = client.post(
        "/v1/sessions",
        json={"agent_id": "oncall-agent", "task_class": "nope", "input": {}},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "task_class_unknown"
