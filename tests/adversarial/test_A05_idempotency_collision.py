"""A05 — Agent-supplied idempotency key is rejected.

Agent invents its own key (not server-issued) → DenyClosed("idempotency_*").
"""

from __future__ import annotations


def test_a05_agent_forged_idempotency_rejected(acp_app):
    app, _ = acp_app
    from fastapi.testclient import TestClient
    client = TestClient(app)

    sess = client.post(
        "/v1/sessions",
        json={"agent_id": "demo-oncall", "task_class": "triage", "input": {}},
    ).json()

    # Use a 26-char ULID-shaped string the server never issued.
    forged = "01ABCDEFGHJKMNPQRSTVWXYZ23"  # 26 chars, all valid Crockford
    assert len(forged) == 26
    resp = client.post(
        f"/v1/sessions/{sess['run_id']}/tools/vm_query/invoke",
        headers={"Authorization": f"Bearer {sess['bearer']}"},
        json={"args": {"query": "x"}, "intent": "", "idempotency_key": forged},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error_code"].startswith("idempotency_")
