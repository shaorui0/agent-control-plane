"""A04 — Event store isolation.

There is no `/v1/events` path. Agent attempts to write to the wide_events
table via the gateway → 404. Only the Gateway code itself can emit events.
"""

from __future__ import annotations


def test_a04_no_event_endpoint(acp_app):
    app, _ = acp_app
    from fastapi.testclient import TestClient
    client = TestClient(app)

    for path in [
        "/v1/events", "/v1/wide_events", "/v1/event_store",
        "/v1/judgments", "/v1/outcome_signals",
    ]:
        r = client.post(path, json={"event_type": "task_end", "outcome": "ok"})
        assert r.status_code == 404, f"path {path} should not exist (got {r.status_code})"
        g = client.get(path)
        assert g.status_code == 404
