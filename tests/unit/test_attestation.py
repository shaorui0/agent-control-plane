"""Gateway attestation tests — args_hash + agent_claim separation."""

from __future__ import annotations

from acp.events.store import WideEventStore
from acp.gateway.attestation import emit_attested_event
from acp.ids import args_hash


def test_emit_attaches_args_hash(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    ev = emit_attested_event(
        store,
        run_id="R1",
        agent_id="A1",
        task_class="t1",
        model_version="m1",
        step=1,
        event_type="tool_call",
        tool_name="vm_query",
        outcome="ok",
        intent="check cpu",
        agent_claim="all good",
        args={"query": "rate(...)"},
    )
    assert ev.attrs["args_hash"] == args_hash({"query": "rate(...)"})
    assert ev.attrs["attested_by"] == "gateway"
    # agent_claim travels as separate (untrusted) column, not folded into outcome.
    assert ev.outcome == "ok"
    assert ev.agent_claim == "all good"


def test_emit_attaches_result_hash(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    result = {"series": [1, 2, 3]}
    ev = emit_attested_event(
        store,
        run_id="R2",
        agent_id="A1",
        task_class="t1",
        model_version="m1",
        step=1,
        event_type="tool_result",
        tool_name="vm_query",
        outcome="ok",
        result=result,
        latency_ms=12,
        tokens=50,
        cost_usd_micros=120,
    )
    assert ev.attrs["result_hash"] == args_hash(result)
    assert ev.attrs["latency_ms"] == 12
    assert ev.attrs["tokens"] == 50
    assert ev.attrs["cost_usd_micros"] == 120


def test_emit_extra_attrs_merged(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    ev = emit_attested_event(
        store,
        run_id="R3",
        agent_id="A1",
        task_class="t1",
        model_version="m1",
        step=1,
        event_type="tool_call",
        outcome="denied",
        extra_attrs={"reason_code": "tier_too_high"},
    )
    assert ev.attrs["reason_code"] == "tier_too_high"
    assert ev.attrs["attested_by"] == "gateway"
