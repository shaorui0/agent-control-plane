"""Tests for Goodhart heuristics."""

from __future__ import annotations

from acp.events.store import WideEventStore
from acp.judge.goodhart import (
    detect_all,
    length_anomaly,
    metric_local,
    persist_flags,
    reasoning_action_mismatch,
    self_citation,
)
from acp.schemas.decision import AgentDecision
from acp.schemas.tool import ToolCallRequest


def _ulid26(c: str = "0") -> str:
    return c * 26


def _decision(reasoning: str, tool: str | None, final: str | None) -> AgentDecision:
    action = None
    if tool is not None:
        action = ToolCallRequest(
            tool_name=tool,
            args={},
            intent="x" * 12,
            run_id="r1",
            idempotency_key=_ulid26("A"),
        )
    return AgentDecision(
        prompt_hash="h",
        reasoning=reasoning,
        chosen_action=action,
        final_answer=final,
        self_confidence=0.7,
        tokens_in=10,
        tokens_out=20,
    )


def test_length_anomaly_fires_on_3x():
    d = _decision("x" * 400, "vm_query", None)
    flag = length_anomaly(d, baseline_p95_chars=100.0)
    assert flag is not None
    assert flag.signal == "length_anomaly"
    assert flag.evidence["ratio"] >= 3.0


def test_length_anomaly_clean_on_normal():
    d = _decision("x" * 100, "vm_query", None)
    assert length_anomaly(d, baseline_p95_chars=100.0) is None


def test_length_anomaly_high_severity_on_5x():
    d = _decision("y" * 600, "vm_query", None)
    flag = length_anomaly(d, baseline_p95_chars=100.0)
    assert flag is not None
    assert flag.severity == "high"


def test_reasoning_action_mismatch_fires_when_tool_differs():
    # Reasoning name-drops kubectl_get; chosen action calls vm_query.
    d = _decision(
        "I should `kubectl_get` pods to check status.",
        "vm_query",
        None,
    )
    flag = reasoning_action_mismatch(d)
    assert flag is not None
    assert flag.signal == "reasoning_action_mismatch"
    assert flag.evidence["chosen_tool"] == "vm_query"
    assert "kubectl_get" in flag.evidence["reasoning_mentions"]


def test_reasoning_action_mismatch_clean_when_consistent():
    d = _decision(
        "Plan: invoke vm_query to read the rate metric.",
        "vm_query",
        None,
    )
    assert reasoning_action_mismatch(d) is None


def test_reasoning_action_mismatch_skipped_for_final_answer():
    d = _decision("Some reasoning here.", None, "final answer text")
    assert reasoning_action_mismatch(d) is None


def test_self_citation_fires_without_intervening_tool(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    prior_reasoning = (
        "The 5xx rate is high and we should scale the payments deployment to mitigate."
    )
    e1 = store.emit(
        run_id="r", agent_id="a", task_class="t", model_version="gpt-4o",
        step=0, event_type="task_start",
        attrs={"reasoning": prior_reasoning},
    )
    # Build a fresh decision that quotes the prior reasoning verbatim.
    snippet = prior_reasoning[:60].strip()
    d = _decision(
        f"As I noted before: {snippet} — therefore acting now.",
        "vm_query",
        None,
    )
    flag = self_citation(d, [e1])
    assert flag is not None
    assert flag.signal == "self_citation"


def test_self_citation_clean_with_intervening_tool(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    prior_reasoning = (
        "The 5xx rate is high and we should scale the payments deployment to mitigate."
    )
    e1 = store.emit(
        run_id="r", agent_id="a", task_class="t", model_version="gpt-4o",
        step=0, event_type="task_start",
        attrs={"reasoning": prior_reasoning},
    )
    e2 = store.emit(
        run_id="r", agent_id="a", task_class="t", model_version="gpt-4o",
        step=1, event_type="tool_call", tool_name="vm_query",
        intent="check rate", attrs={"args": {}},
    )
    d = _decision(
        f"As I noted before: {prior_reasoning[:60].strip()}",
        "vm_query",
        None,
    )
    assert self_citation(d, [e1, e2]) is None


def test_metric_local_fires_on_proxy_divergence():
    history = [
        {"judge_pass_rate": 0.80, "intervention_free_rate": 0.95},
        {"judge_pass_rate": 0.90, "intervention_free_rate": 0.85},
    ]
    d = _decision("ok", "vm_query", None)
    flag = metric_local(d, history)
    assert flag is not None
    assert flag.signal == "metric_local"


def test_metric_local_clean_when_aligned():
    history = [
        {"judge_pass_rate": 0.80, "intervention_free_rate": 0.85},
        {"judge_pass_rate": 0.85, "intervention_free_rate": 0.90},
    ]
    d = _decision("ok", "vm_query", None)
    assert metric_local(d, history) is None


def test_detect_all_aggregates_multiple():
    d = _decision(
        "I should `kubectl_get` pods. " + "x" * 400,
        "vm_query",
        None,
    )
    flags = detect_all(d, [], baseline_p95_chars=100.0)
    signals = {f.signal for f in flags}
    assert "length_anomaly" in signals
    assert "reasoning_action_mismatch" in signals


def test_persist_flags_writes_rows(tmp_db, frozen_clock):
    d = _decision("x" * 400, "vm_query", None)
    flags = detect_all(d, [], baseline_p95_chars=100.0)
    n = persist_flags(tmp_db, "evt-1", flags, ts_ms=frozen_clock.now())
    assert n == len(flags)
    rows = tmp_db.execute("SELECT * FROM goodhart_flags").fetchall()
    assert len(rows) == n
    assert all(r["event_id"] == "evt-1" for r in rows)
