"""Scenario 04 — agent tries `kubectl_scale --replicas_delta=99`, denied by kwargs.

The binding has kwargs_constraints.max_replicas_delta=2; the agent asks for 99
→ DenyClosed("kwargs_constraint_violation"). Wide event records denial.
"""

from __future__ import annotations

import pytest

from acp.errors import DenyClosed
from acp.events.query import EventQuery


@pytest.mark.asyncio
async def test_irreversible_temptation_denied(acp_app, local_client):
    app, deps = acp_app

    sess = await local_client.start_session("demo-oncall", "triage", {})
    run_id = sess["run_id"]

    with pytest.raises(DenyClosed) as ei:
        await local_client.invoke_tool(
            run_id, "kubectl_scale",
            {"deployment": "payments-api", "namespace": "payments", "replicas_delta": 99},
            intent="scale payments to handle traffic spike",
        )
    assert ei.value.reason_code == "kwargs_constraint_violation"

    q = EventQuery(deps.conn)
    denied = [e for e in q.by_run(run_id) if e.outcome == "denied"]
    assert len(denied) == 1
    assert denied[0].attrs.get("reason_code") == "kwargs_constraint_violation"
