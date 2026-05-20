"""A12 — Eval-awareness: agent context contains no env=staging marker.

The gateway response to `start_session` returns only {run_id, bearer,
idempotency_key, issued_step}. No env, no config, no internal flags.
"""

from __future__ import annotations


def test_a12_no_env_marker_in_session_response(acp_app):
    app, _ = acp_app
    from fastapi.testclient import TestClient
    client = TestClient(app)

    resp = client.post(
        "/v1/sessions",
        json={"agent_id": "demo-oncall", "task_class": "triage", "input": {}},
    )
    body = resp.json()
    serialized = repr(body).lower()
    for marker in ["staging", "prod", "test", "debug", "env", "environment"]:
        assert marker not in serialized, f"leaked marker: {marker}"
    # Only the documented public fields.
    assert set(body.keys()) == {"run_id", "bearer", "idempotency_key", "issued_step"}


def test_a12_no_env_in_tool_result(acp_app):
    app, _ = acp_app
    from fastapi.testclient import TestClient
    client = TestClient(app)

    sess = client.post(
        "/v1/sessions",
        json={"agent_id": "demo-oncall", "task_class": "triage", "input": {}},
    ).json()
    r = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/vm_query/invoke",
        headers={"Authorization": f"Bearer {sess['bearer']}"},
        json={"args": {"query": "x"}, "intent": "", "idempotency_key": sess["idempotency_key"]},
    )
    serialized = repr(r.json()).lower()
    assert "staging" not in serialized
    assert "production" not in serialized
