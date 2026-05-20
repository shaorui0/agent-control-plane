"""Scenario 02 (silent fail) — pass judgment that flips after oncall_refire.

Original judgment is `pass`. We then post an OutcomeSignal(kind=oncall_refire)
within the 24h window and call `feedback.maybe_flip_verdict`. Assert the
judgment row is now retroactively_flipped=1 AND a new wide_event `outcome` is
emitted with `retroactive_fail=True`.
"""

from __future__ import annotations

import pytest

from acp.clock import FrozenClock
from acp.events.query import EventQuery
from acp.judge.llm_clients import StubJudge
from acp.judge.pipeline import JudgePipeline
from acp.schemas.outcome import OutcomeSignal
from acp.slo.feedback import ingest_outcome_signal, maybe_flip_verdict


@pytest.mark.asyncio
async def test_silent_fail_retroactive_flip(acp_app, local_client, frozen_clock):
    app, deps = acp_app

    sess = await local_client.start_session("demo-oncall", "triage", {"alert": "cpu_spike"})
    run_id = sess["run_id"]
    await local_client.invoke_tool(run_id, "vm_query", {"query": "cpu_usage"})
    await local_client.end_session(
        run_id, final_output={"summary": "resolved"}, agent_claim_outcome="resolved",
    )

    q = EventQuery(deps.conn)
    pipeline = JudgePipeline(
        event_store=deps.events, query=q, registry_store=deps.registry,
        judges=[StubJudge("A"), StubJudge("B")],
    )
    panel = await pipeline.judge_task(run_id)
    assert panel.final_label == "pass"

    # 20 minutes later: oncall refires (signal from outside world).
    later = FrozenClock(at_ms=frozen_clock.now() + 20 * 60 * 1000)
    signal = OutcomeSignal(
        signal_id="sig-refire-01", run_id=run_id, kind="oncall_refire",
        value_json={"alert": "cpu_spike_v2"}, delay_seconds=1200,
        source="alertmanager", ts=later.now(),
    )
    ingest_outcome_signal(signal, deps.conn)

    flipped = maybe_flip_verdict(run_id, deps.conn, later)
    assert flipped is True

    # judgments row now flipped.
    row = deps.conn.execute(
        "SELECT verdict, retroactively_flipped FROM judgments "
        "WHERE event_id IN (SELECT event_id FROM wide_events WHERE run_id=?) "
        "ORDER BY ts DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    assert row["verdict"] == "fail"
    assert row["retroactively_flipped"] == 1

    # New outcome wide_event with retroactive_fail.
    outcomes = [e for e in q.by_run(run_id) if e.event_type == "outcome"]
    assert len(outcomes) == 1
    assert outcomes[0].attrs.get("retroactive_fail") is True
