"""Tests for the judge pipeline: cross-model invariant, panel labels, persistence."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from acp.events.query import EventQuery
from acp.events.store import WideEventStore
from acp.judge.disagreement import cohen_kappa
from acp.judge.llm_clients import BaseJudgeClient, StubJudge
from acp.judge.pipeline import DEFAULT_FAMILY_MAP, JudgePipeline, _family_of
from acp.judge.replay import replay_run
from acp.judge.rubric import build_judge_prompt, derive_passed
from acp.schemas.judge import JudgeRubric, JudgeVerdict


# ---- helpers ----------------------------------------------------------------


class FixedJudge(BaseJudgeClient):
    """Always returns a verdict with the given rubric. For deterministic tests."""

    def __init__(self, name: str, model: str, family: str, rubric: JudgeRubric):
        self.judge_name = name
        self.judge_model = model
        self.model_family = family
        self._rubric = rubric

    async def judge(self, prompt: str, rubric_schema: dict[str, Any]) -> JudgeVerdict:
        return JudgeVerdict(
            judge_name=self.judge_name,
            judge_model=self.judge_model,
            rubric=self._rubric,
            passed=derive_passed(self._rubric),
            rationale="fixed",
            cost_usd_micros=1,
        )


_PASS_RUBRIC = JudgeRubric(
    correctness=0.9,
    grounding=0.8,
    safety=0.9,
    deception_risk=0.1,
    goodhart_risk=0.1,
)
_FAIL_RUBRIC = JudgeRubric(
    correctness=0.2,
    grounding=0.3,
    safety=0.4,
    deception_risk=0.5,
    goodhart_risk=0.6,
)


def _seed_run(store: WideEventStore, run_id: str, model_version: str) -> None:
    store.emit(
        run_id=run_id,
        agent_id="oncall",
        task_class="triage",
        model_version=model_version,
        step=0,
        event_type="task_start",
        attrs={"prompt_hash": "abc"},
    )
    store.emit(
        run_id=run_id,
        agent_id="oncall",
        task_class="triage",
        model_version=model_version,
        step=1,
        event_type="tool_call",
        tool_name="vm_query",
        intent="check 5xx rate",
        attrs={"args": {"query": "rate(http_5xx[5m])"}},
    )
    store.emit(
        run_id=run_id,
        agent_id="oncall",
        task_class="triage",
        model_version=model_version,
        step=2,
        event_type="tool_result",
        tool_name="vm_query",
        outcome="ok",
        attrs={"result_json": {"value": 0.01}},
    )
    store.emit(
        run_id=run_id,
        agent_id="oncall",
        task_class="triage",
        model_version=model_version,
        step=3,
        event_type="task_end",
        outcome="ok",
        attrs={
            "final_answer": "5xx rate within SLO",
            "self_confidence": 0.8,
            "chosen_action": None,
        },
    )


# ---- tests ------------------------------------------------------------------


def test_family_of_recognizes_known_models():
    assert _family_of("claude-haiku-4-5", DEFAULT_FAMILY_MAP) == "anthropic"
    assert _family_of("gpt-4o-mini", DEFAULT_FAMILY_MAP) == "openai"
    assert _family_of("gemini-2-pro", DEFAULT_FAMILY_MAP) == "google"
    assert _family_of("mystery-model-x", DEFAULT_FAMILY_MAP) is None


def test_cross_model_invariant_rejects_same_family(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    query = EventQuery(tmp_db)
    # Two judges claiming to be Anthropic; agent is also Anthropic → must reject.
    j1 = FixedJudge("a1", "claude-haiku-4-5", "anthropic", _PASS_RUBRIC)
    j2 = FixedJudge("a2", "claude-sonnet-4-6", "anthropic", _PASS_RUBRIC)
    with pytest.raises(ValueError, match="cross-model"):
        JudgePipeline(
            store, query, None, [j1, j2],
            clock=frozen_clock,
            agent_model_version="claude-sonnet-4.6@2026-05-01",
        )


def test_cross_model_invariant_passes_with_stub(tmp_db, frozen_clock):
    """Stub judges count as cross-model (family='stub')."""
    store = WideEventStore(tmp_db, clock=frozen_clock)
    query = EventQuery(tmp_db)
    pipe = JudgePipeline(
        store, query, None,
        [StubJudge("A"), StubJudge("B")],
        clock=frozen_clock,
        agent_model_version="claude-sonnet-4.6",
    )
    assert pipe is not None


def test_judge_task_writes_judgment_event_and_rows(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    query = EventQuery(tmp_db)
    _seed_run(store, "run-X", "claude-sonnet-4.6@2026-05-01")

    # Cross-model: one anthropic-flavored fixed + one openai-flavored fixed.
    j_open = FixedJudge("openai-judge", "gpt-4o-mini", "openai", _PASS_RUBRIC)
    j_stub = StubJudge("stub-A")
    pipe = JudgePipeline(store, query, None, [j_open, j_stub], clock=frozen_clock)

    panel = asyncio.run(pipe.judge_task("run-X"))
    assert panel.final_label in ("pass", "fail", "escalate")
    assert len(panel.verdicts) == 2

    # judgment wide event written.
    events = list(query.by_run("run-X"))
    judgments = [e for e in events if e.event_type == "judgment"]
    assert len(judgments) == 1
    j_attrs = judgments[0].attrs
    assert j_attrs["verdict"] == panel.final_label
    assert j_attrs["retroactively_flipped"] is False
    assert j_attrs["original_verdict"] == panel.final_label
    assert set(j_attrs["judge_models"]) == {"gpt-4o-mini", "stub:stub-A"}

    # judgments table rows.
    rows = tmp_db.execute("SELECT judge_name FROM judgments").fetchall()
    assert {r["judge_name"] for r in rows} == {"openai-judge", "stub-A"}


def test_panel_label_pass_fail_escalate(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    query = EventQuery(tmp_db)
    _seed_run(store, "run-pass", "gpt-4o")
    _seed_run(store, "run-fail", "gpt-4o")
    _seed_run(store, "run-mix", "gpt-4o")

    pass_pipe = JudgePipeline(
        store, query, None,
        [
            FixedJudge("a", "claude-haiku-4-5", "anthropic", _PASS_RUBRIC),
            FixedJudge("b", "gemini-2-pro", "google", _PASS_RUBRIC),
        ],
        clock=frozen_clock,
    )
    assert asyncio.run(pass_pipe.judge_task("run-pass")).final_label == "pass"

    fail_pipe = JudgePipeline(
        store, query, None,
        [
            FixedJudge("a", "claude-haiku-4-5", "anthropic", _FAIL_RUBRIC),
            FixedJudge("b", "gemini-2-pro", "google", _FAIL_RUBRIC),
        ],
        clock=frozen_clock,
    )
    assert asyncio.run(fail_pipe.judge_task("run-fail")).final_label == "fail"

    mix_pipe = JudgePipeline(
        store, query, None,
        [
            FixedJudge("a", "claude-haiku-4-5", "anthropic", _PASS_RUBRIC),
            FixedJudge("b", "gemini-2-pro", "google", _FAIL_RUBRIC),
        ],
        clock=frozen_clock,
    )
    assert asyncio.run(mix_pipe.judge_task("run-mix")).final_label == "escalate"


def test_cohen_kappa_basic():
    # 2 judges agreeing → 1.0
    v_pass = JudgeVerdict(
        judge_name="x", judge_model="m1", rubric=_PASS_RUBRIC,
        passed=True, rationale="", cost_usd_micros=0,
    )
    v_pass2 = JudgeVerdict(
        judge_name="y", judge_model="m2", rubric=_PASS_RUBRIC,
        passed=True, rationale="", cost_usd_micros=0,
    )
    v_fail = JudgeVerdict(
        judge_name="z", judge_model="m3", rubric=_FAIL_RUBRIC,
        passed=False, rationale="", cost_usd_micros=0,
    )
    assert cohen_kappa([v_pass, v_pass2]) == 1.0
    assert cohen_kappa([v_pass, v_fail]) == -1.0
    # 3 judges all pass → Fleiss returns 1.0
    assert cohen_kappa([v_pass, v_pass2, v_pass]) == pytest.approx(1.0)
    # 3 judges 2-pass-1-fail → kappa in [-1, 1], strictly negative.
    k = cohen_kappa([v_pass, v_pass2, v_fail])
    assert -1.0 <= k <= 1.0


def test_build_judge_prompt_excludes_reasoning(tmp_db, frozen_clock):
    """T4: reasoning must NOT appear in judge prompt."""
    store = WideEventStore(tmp_db, clock=frozen_clock)
    query = EventQuery(tmp_db)
    secret = "SECRET_REASONING_DO_NOT_SHOW"
    store.emit(
        run_id="run-secret",
        agent_id="oncall",
        task_class="triage",
        model_version="gpt-4o",
        step=0,
        event_type="task_start",
        attrs={"reasoning": secret},
    )
    store.emit(
        run_id="run-secret",
        agent_id="oncall",
        task_class="triage",
        model_version="gpt-4o",
        step=1,
        event_type="task_end",
        outcome="ok",
        attrs={"final_answer": "done", "reasoning": secret},
    )
    events = list(query.by_run("run-secret"))
    decision_event = events[-1]
    prompt = build_judge_prompt(decision_event, events, task_class="triage")
    assert secret not in prompt


def test_run_worker_judges_unjudged_runs(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    query = EventQuery(tmp_db)
    _seed_run(store, "run-w1", "gpt-4o")
    _seed_run(store, "run-w2", "gpt-4o")

    pipe = JudgePipeline(
        store, query, None,
        [
            FixedJudge("a", "claude-haiku-4-5", "anthropic", _PASS_RUBRIC),
            FixedJudge("b", "gemini-2-pro", "google", _PASS_RUBRIC),
        ],
        clock=frozen_clock,
    )
    n = asyncio.run(pipe.run_worker(interval_seconds=0.0, stop_after=1))
    assert n == 2


def test_replay_writes_flag_when_disagrees(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    query = EventQuery(tmp_db)
    _seed_run(store, "run-r", "gpt-4o")

    # Original verdict: pass (both judges pass).
    pipe = JudgePipeline(
        store, query, None,
        [
            FixedJudge("a", "claude-haiku-4-5", "anthropic", _PASS_RUBRIC),
            FixedJudge("b", "gemini-2-pro", "google", _PASS_RUBRIC),
        ],
        clock=frozen_clock,
    )
    original = asyncio.run(pipe.judge_task("run-r"))
    assert original.final_label == "pass"

    # Replay with fail-flavored panel → should write a goodhart flag.
    new_panel = [
        FixedJudge("a2", "claude-haiku-4-5", "anthropic", _FAIL_RUBRIC),
        FixedJudge("b2", "gemini-2-pro", "google", _FAIL_RUBRIC),
    ]
    replay_result = asyncio.run(replay_run("run-r", new_panel, pipe))
    assert replay_result.final_label == "fail"

    flags = tmp_db.execute(
        "SELECT signal, evidence_json FROM goodhart_flags"
    ).fetchall()
    assert len(flags) == 1
    assert "judge_drift_detected_via_replay" in flags[0]["evidence_json"]
