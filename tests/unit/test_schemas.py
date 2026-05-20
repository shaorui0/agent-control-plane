"""Round-trip and validation tests for all ACP schemas."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from acp.schemas import (
    AgentDecision,
    AgentSpec,
    ApprovalRequest,
    AuditFinding,
    AutonomyTier,
    AutonomyTierChange,
    BaseEvent,
    BudgetSnapshot,
    BurnRateWindow,
    GoodhartFlag,
    IntentProof,
    JudgePanelResult,
    JudgeRubric,
    JudgeVerdict,
    OutcomeSignal,
    SLODefinition,
    TaskClassConfig,
    ToolBinding,
    ToolCallRequest,
    ToolCallResult,
    ToolSpec,
    WideEvent,
    from_db_row,
    to_db_row,
)


# --- Helpers ----------------------------------------------------------------


VALID_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _task_class() -> TaskClassConfig:
    return TaskClassConfig(
        name="oncall_triage",
        slo_sli_kind="judge_pass_rate",
        slo_target=0.95,
        slo_window="7d",
    )


def _tool_binding() -> ToolBinding:
    return ToolBinding(name="vm_query", max_tier=AutonomyTier.T1)


def _agent_spec() -> AgentSpec:
    return AgentSpec(
        agent_id="oncall",
        owner="sre@example.com",
        version="0.1.0",
        model_version="claude-sonnet-4.6",
        description="",
        task_classes=[_task_class()],
        sealed_tools=[_tool_binding()],
        budget_hourly_usd=1.0,
        budget_hourly_tokens=1_000_000,
    )


def _good_rubric() -> JudgeRubric:
    return JudgeRubric(
        correctness=0.9,
        grounding=0.8,
        safety=0.9,
        deception_risk=0.1,
        goodhart_risk=0.1,
    )


def _bad_rubric() -> JudgeRubric:
    return JudgeRubric(
        correctness=0.3,
        grounding=0.5,
        safety=0.4,
        deception_risk=0.6,
        goodhart_risk=0.5,
    )


def _wide_event(**overrides) -> WideEvent:
    base = dict(
        event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        prev_event_id=None,
        ts=1_700_000_000_000,
        run_id="run-1",
        agent_id="oncall",
        task_class="oncall_triage",
        model_version="claude-sonnet-4.6",
        step=0,
        event_type="task_start",
        tool_name=None,
        tier_required=None,
        outcome=None,
        intent=None,
        agent_claim=None,
        attrs={"foo": "bar", "n": 1},
        chain_hash="deadbeef",
    )
    base.update(overrides)
    return WideEvent(**base)


# --- Frozenness + extra=forbid ----------------------------------------------


def test_frozen_models_reject_mutation():
    tb = _tool_binding()
    with pytest.raises((ValidationError, TypeError)):
        tb.name = "other"  # type: ignore[misc]


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        ToolBinding(name="x", max_tier=AutonomyTier.T1, surprise=True)  # type: ignore[call-arg]


# --- Round-trip via model_dump / model_validate -----------------------------


@pytest.mark.parametrize(
    "instance",
    [
        _task_class(),
        _tool_binding(),
        _agent_spec(),
        IntentProof(text="ten chars."),
        ToolCallRequest(
            tool_name="vm_query",
            args={"q": "up"},
            intent="check uptime",
            run_id="run-1",
            idempotency_key=VALID_ULID,
        ),
        ToolCallResult(
            tool_name="vm_query",
            args_hash="abc",
            result_json={"ok": True},
            error=None,
            latency_ms=12,
            cost_usd_micros=0,
        ),
        ToolSpec(
            name="vm_query",
            description="",
            input_schema={},
            output_schema={},
            tier="T1",
            reversibility="read",
            blast_radius="self",
        ),
        AgentDecision(
            prompt_hash="h",
            reasoning="r",
            chosen_action=None,
            final_answer="done",
            self_confidence=0.8,
            tokens_in=10,
            tokens_out=20,
        ),
        _good_rubric(),
        JudgeVerdict(
            judge_name="panel-a",
            judge_model="gpt-4o",
            rubric=_good_rubric(),
            passed=True,
            rationale="ok",
            cost_usd_micros=100,
        ),
        JudgeVerdict(
            judge_name="panel-b",
            judge_model="gpt-4o",
            rubric=_bad_rubric(),
            passed=False,
            rationale="bad",
            cost_usd_micros=100,
        ),
        JudgePanelResult(
            verdicts=[
                JudgeVerdict(
                    judge_name="a",
                    judge_model="gpt-4o",
                    rubric=_good_rubric(),
                    passed=True,
                    rationale="",
                    cost_usd_micros=0,
                )
            ],
            agreement_kappa=1.0,
            final_label="pass",
        ),
        GoodhartFlag(signal="length_anomaly", evidence={"n": 1}, severity="low"),
        SLODefinition(
            agent_id="a",
            task_class="t",
            model_version="m",
            sli_kind="judge_pass_rate",
            target=0.9,
            window_seconds=3600,
        ),
        BurnRateWindow(label="1h", window_seconds=3600, sli_value=0.95, target=0.99, burn_rate=2.0),
        BudgetSnapshot(
            snapshot_id="s",
            ts=1,
            agent_id="a",
            task_class="t",
            model_version="m",
            window_label="1h",
            budget_class="organic",
            sli_value=0.9,
            slo_target=0.99,
            burn_rate=2.0,
            budget_remaining=0.5,
        ),
        OutcomeSignal(
            signal_id="o",
            run_id="r",
            kind="git_applied",
            value_json={"sha": "abc"},
            delay_seconds=10,
            source="github",
            ts=1,
        ),
        ApprovalRequest(
            approval_id="ap",
            event_id="ev",
            agent_id="a",
            tool_name="kubectl_scale",
            intent="scale down",
            args_json={"replicas": 0},
        ),
        AuditFinding(audit_id="au", event_id="ev", reason="sample"),
        AutonomyTierChange(
            agent_id="a",
            task_class="t",
            old_tier=AutonomyTier.T1,
            new_tier=AutonomyTier.T0,
            cause="burn",
            burn_rate=4.0,
            boundary_delta=None,
            ts=1,
        ),
        _wide_event(),
    ],
)
def test_roundtrip_model_dump(instance):
    cls = type(instance)
    dumped = instance.model_dump()
    rebuilt = cls.model_validate(dumped)
    assert rebuilt == instance


# --- BaseEvent --------------------------------------------------------------


def test_base_event_blank_rejected():
    with pytest.raises(ValidationError):
        BaseEvent(
            event_id="   ",
            ts=0,
            run_id="r",
            agent_id="a",
            task_class="t",
            model_version="m",
            step=0,
            event_type="task_start",
        )


# --- AgentDecision invariants -----------------------------------------------


def test_decision_requires_exactly_one():
    with pytest.raises(ValidationError):
        AgentDecision(
            prompt_hash="h",
            reasoning="",
            chosen_action=None,
            final_answer=None,
            self_confidence=0.5,
            tokens_in=0,
            tokens_out=0,
        )


def test_decision_rejects_both():
    req = ToolCallRequest(
        tool_name="vm_query",
        args={},
        intent="x",
        run_id="r",
        idempotency_key=VALID_ULID,
    )
    with pytest.raises(ValidationError):
        AgentDecision(
            prompt_hash="h",
            reasoning="",
            chosen_action=req,
            final_answer="also set",
            self_confidence=0.5,
            tokens_in=0,
            tokens_out=0,
        )


# --- JudgeVerdict derivation -------------------------------------------------


def test_verdict_passed_must_match_rubric():
    with pytest.raises(ValidationError):
        JudgeVerdict(
            judge_name="j",
            judge_model="m",
            rubric=_good_rubric(),
            passed=False,  # lies about rubric
            rationale="",
            cost_usd_micros=0,
        )


# --- ULID idempotency key ----------------------------------------------------


def test_idempotency_key_must_be_ulid():
    with pytest.raises(ValidationError):
        ToolCallRequest(
            tool_name="vm_query",
            args={},
            intent="x",
            run_id="r",
            idempotency_key="short",
        )


# --- AgentSpec owner email ---------------------------------------------------


def test_agent_spec_owner_must_be_email():
    with pytest.raises(ValidationError):
        AgentSpec(
            agent_id="a",
            owner="not-an-email",
            version="1",
            model_version="m",
            description="",
            task_classes=[_task_class()],
            sealed_tools=[_tool_binding()],
            budget_hourly_usd=1.0,
            budget_hourly_tokens=1,
        )


# --- WideEvent DB roundtrip --------------------------------------------------


def test_wide_event_db_roundtrip():
    ev = _wide_event(
        tool_name="vm_query",
        tier_required="T1",
        outcome="ok",
        intent="check up",
        agent_claim="claim",
        event_type="tool_call",
    )
    row = to_db_row(ev)
    assert isinstance(row["attrs_json"], str)
    back = from_db_row(row)
    assert back == ev


def test_wide_event_db_roundtrip_empty_attrs():
    ev = _wide_event(attrs={})
    back = from_db_row(to_db_row(ev))
    assert back == ev


# --- Hypothesis fuzz: WideEvent attrs roundtrip ------------------------------


@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
@given(
    attrs=st.dictionaries(
        keys=st.text(min_size=1, max_size=10),
        values=st.one_of(
            st.integers(min_value=-(10**6), max_value=10**6),
            st.text(max_size=20),
            st.booleans(),
            st.none(),
        ),
        max_size=5,
    ),
    step=st.integers(min_value=0, max_value=1000),
    ts=st.integers(min_value=0, max_value=10**13),
)
def test_wide_event_fuzz_roundtrip(attrs, step, ts):
    ev = _wide_event(attrs=attrs, step=step, ts=ts)
    back = from_db_row(to_db_row(ev))
    assert back == ev
    # JSON serializable
    dumped = ev.model_dump()
    rebuilt = WideEvent.model_validate(dumped)
    assert rebuilt == ev
