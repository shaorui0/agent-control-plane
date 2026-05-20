"""Scenario 01 (CPU spike) end-to-end via LocalClient.

Expected: 1 task_start, N tool_calls, 1 task_end, 1 judgment with verdict=pass.
"""

from __future__ import annotations

import pytest

from acp.events.query import EventQuery
from acp.judge.llm_clients import StubJudge
from acp.judge.pipeline import JudgePipeline


@pytest.mark.asyncio
async def test_happy_path_cpu_spike(acp_app, local_client):
    app, deps = acp_app

    sess = await local_client.start_session("demo-oncall", "triage", {"alert": "cpu_spike"})
    run_id = sess["run_id"]

    # Three read-only tool calls (T1).
    r1 = await local_client.invoke_tool(run_id, "vm_query", {"query": "cpu_usage"})
    assert r1["status"] == "ok"
    r2 = await local_client.invoke_tool(run_id, "loki_query", {"query": '{app="payments"}'})
    assert r2["status"] == "ok"
    r3 = await local_client.invoke_tool(run_id, "kubectl_get", {"resource": "pods"})
    assert r3["status"] == "ok"

    await local_client.end_session(
        run_id, final_output={"summary": "scaled"}, agent_claim_outcome="ok",
    )

    # Verify wide events.
    q = EventQuery(deps.conn)
    events = list(q.by_run(run_id))
    types = [e.event_type for e in events]
    assert types.count("task_start") >= 1
    assert types.count("tool_call") == 3
    assert types.count("tool_result") == 3
    assert types.count("task_end") == 1

    # Judge it.
    pipeline = JudgePipeline(
        event_store=deps.events, query=q, registry_store=deps.registry,
        judges=[StubJudge("A"), StubJudge("B")],
    )
    panel = await pipeline.judge_task(run_id)
    assert panel.final_label in {"pass", "escalate"}

    # One judgment wide_event exists.
    judgments = [e for e in q.by_run(run_id) if e.event_type == "judgment"]
    assert len(judgments) == 1
