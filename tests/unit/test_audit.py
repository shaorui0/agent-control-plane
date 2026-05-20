"""Audit queue tests: submit persists calibration + closes the audit row."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acp.events.store import WideEventStore
from acp.human.audit import AuditQueue, build_audit_router
from acp.ids import new_ulid


@pytest.fixture
def setup(tmp_db, frozen_clock):
    events = WideEventStore(tmp_db, clock=frozen_clock)
    # Emit a judgment event to anchor calibration.
    run_id = "RUNAUDIT01234567890123456789"
    events.emit(
        run_id=run_id,
        agent_id="a",
        task_class="triage",
        model_version="claude-sonnet-4-7",
        step=1,
        event_type="task_start",
        outcome="ok",
    )
    judgment = events.emit(
        run_id=run_id,
        agent_id="a",
        task_class="triage",
        model_version="claude-sonnet-4-7",
        step=2,
        event_type="judgment",
        outcome="ok",
        attrs={
            "verdict": "pass",
            "judge_models": ["stub:stub-A", "stub:stub-B"],
        },
    )
    audit_id = new_ulid()
    tmp_db.execute(
        "INSERT INTO audit_queue (audit_id, event_id, reason, status) "
        "VALUES (?, ?, 'sample', 'pending')",
        (audit_id, judgment.event_id),
    )
    tmp_db.commit()

    queue = AuditQueue(tmp_db, clock=frozen_clock)
    return tmp_db, queue, audit_id, judgment.event_id


def test_list_pending(setup):
    _, queue, audit_id, _ = setup
    items = queue.list_pending()
    assert len(items) == 1
    assert items[0].audit_id == audit_id
    assert items[0].reason == "sample"


def test_submit_persists_calibration(setup):
    conn, queue, audit_id, event_id = setup
    updated = queue.submit(audit_id, human_label="pass", notes="agrees", reviewer="alice@x")
    assert updated.status == "reviewed"
    assert updated.human_label == "pass"

    # Calibration row exists with delta=0 (panel said pass, human said pass).
    rows = conn.execute(
        "SELECT judge_panel_label, human_label, delta, judge_model, task_class "
        "FROM calibration WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["judge_panel_label"] == "pass"
    assert rows[0]["human_label"] == "pass"
    assert rows[0]["delta"] == 0
    assert rows[0]["task_class"] == "triage"
    assert rows[0]["judge_model"].startswith("stub:")


def test_submit_disagreement_yields_delta_one(setup):
    conn, queue, audit_id, event_id = setup
    queue.submit(audit_id, human_label="fail", notes="judge wrong", reviewer="alice@x")
    row = conn.execute(
        "SELECT delta FROM calibration WHERE event_id = ?", (event_id,)
    ).fetchone()
    assert row["delta"] == 1


def test_submit_unknown_raises(setup):
    _, queue, _, _ = setup
    with pytest.raises(KeyError):
        queue.submit("ghost", "pass", "", "alice@x")


def test_submit_invalid_label_raises(setup):
    _, queue, audit_id, _ = setup
    with pytest.raises(ValueError):
        queue.submit(audit_id, "maybe", "", "alice@x")


def test_http_route(setup):
    _, queue, audit_id, _ = setup
    app = FastAPI()
    app.include_router(build_audit_router(queue))
    client = TestClient(app)

    r = client.get("/v1/audit")
    assert r.status_code == 200
    assert any(item["audit_id"] == audit_id for item in r.json())

    r2 = client.post(
        f"/v1/audit/{audit_id}/decide",
        json={"human_label": "pass", "reviewer": "alice@x"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["human_label"] == "pass"
