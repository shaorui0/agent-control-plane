"""A03 — Tier denial leaks no internal detail.

Agent (at T1) tries to invoke a T3 tool. The response body must contain only
the scrubbed `{error_code, message}` envelope with NO tier_required, NO
binding info, NO traceback.
"""

from __future__ import annotations

from acp.schemas.agent import AutonomyTier


def test_a03_tier_denial_envelope_clean(acp_app):
    app, deps = acp_app
    from fastapi.testclient import TestClient

    class _T1:
        def current_tier(self, agent_id: str, task_class: str) -> AutonomyTier:
            return AutonomyTier.T1

    deps.autonomy = _T1()
    client = TestClient(app)

    sess = client.post(
        "/v1/sessions",
        json={"agent_id": "demo-oncall", "task_class": "triage", "input": {}},
    ).json()
    resp = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/kubectl_scale/invoke",
        headers={"Authorization": f"Bearer {sess['bearer']}"},
        json={
            "args": {"deployment": "p", "replicas_delta": 1},
            "intent": "scale payments for spike",
            "idempotency_key": sess["idempotency_key"],
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    detail = body.get("detail", {})
    # Only the scrubbed envelope is allowed.
    assert set(detail.keys()) == {"error_code", "message"}
    assert detail["error_code"] == "tier_too_high"
    # No tier numbers, no traceback, no internal fields.
    serialized = repr(body).lower()
    assert "t3" not in serialized
    assert "traceback" not in serialized
    assert "binding" not in serialized
