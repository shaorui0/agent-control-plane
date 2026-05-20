"""100% coverage of acp.gateway.policy."""

from __future__ import annotations

import pytest

from acp.errors import BudgetExceeded, DenyClosed
from acp.gateway.policy import (
    BudgetState,
    budget_check,
    intent_check,
    kwargs_check,
    seal_check,
    tier_check,
)
from acp.schemas.agent import AutonomyTier, ToolBinding


def mk_binding(**kw):
    base = dict(name="kubectl_scale", max_tier=AutonomyTier.T2, requires_intent=True)
    base.update(kw)
    return ToolBinding(**base)


# ---------------- seal_check ----------------


def test_seal_check_ok():
    seal_check(mk_binding(), "kubectl_scale")


def test_seal_check_none_binding():
    with pytest.raises(DenyClosed) as e:
        seal_check(None, "kubectl_scale")
    assert e.value.reason_code == "tool_not_sealed"


def test_seal_check_name_mismatch():
    with pytest.raises(DenyClosed) as e:
        seal_check(mk_binding(name="vm_query"), "kubectl_scale")
    assert e.value.reason_code == "tool_not_sealed"


# ---------------- tier_check ----------------


def test_tier_check_ok_when_cap_le_current():
    # binding caps at T1; current is T2 -> allowed (binding more restricted)
    tier_check(mk_binding(max_tier=AutonomyTier.T1), AutonomyTier.T2)


def test_tier_check_denies_when_cap_gt_current():
    with pytest.raises(DenyClosed) as e:
        tier_check(mk_binding(max_tier=AutonomyTier.T3), AutonomyTier.T1)
    assert e.value.reason_code == "tier_too_high"


def test_tier_check_accepts_string_tier():
    tier_check(mk_binding(max_tier=AutonomyTier.T1), "T2")


# ---------------- intent_check ----------------


def test_intent_check_not_required_skips():
    intent_check(mk_binding(requires_intent=False), None)
    intent_check(mk_binding(requires_intent=False), "x")


def test_intent_check_blank_when_optional():
    # Provided but blank-string-after-strip with a content-string -> blank deny.
    with pytest.raises(DenyClosed) as e:
        intent_check(mk_binding(requires_intent=False), "    ")
    assert e.value.reason_code == "intent_blank"


def test_intent_check_required_missing():
    with pytest.raises(DenyClosed) as e:
        intent_check(mk_binding(), None)
    assert e.value.reason_code == "intent_missing"


def test_intent_check_required_empty_str():
    with pytest.raises(DenyClosed) as e:
        intent_check(mk_binding(), "   ")
    assert e.value.reason_code == "intent_missing"


def test_intent_check_too_short():
    with pytest.raises(DenyClosed) as e:
        intent_check(mk_binding(), "scale 1")
    assert e.value.reason_code == "intent_too_short"


def test_intent_check_no_verb():
    with pytest.raises(DenyClosed) as e:
        intent_check(mk_binding(), "the quick brown fox xyz qrs")
    assert e.value.reason_code == "intent_no_verb"


def test_intent_check_ok():
    intent_check(mk_binding(), "scale payments-api deployment to handle traffic spike")


# ---------------- kwargs_check ----------------


def test_kwargs_check_no_constraints_passes():
    kwargs_check(mk_binding(kwargs_constraints={}), {"foo": "bar"})


def test_kwargs_check_max_replicas_delta_ok():
    b = mk_binding(kwargs_constraints={"max_replicas_delta": 2})
    kwargs_check(b, {"replicas_delta": 2})
    kwargs_check(b, {"replicas_delta": -2})


def test_kwargs_check_max_replicas_delta_violation():
    b = mk_binding(kwargs_constraints={"max_replicas_delta": 2})
    with pytest.raises(DenyClosed) as e:
        kwargs_check(b, {"replicas_delta": 5})
    assert e.value.reason_code == "kwargs_constraint_violation"


def test_kwargs_check_max_replicas_delta_non_int():
    b = mk_binding(kwargs_constraints={"max_replicas_delta": 2})
    with pytest.raises(DenyClosed) as e:
        kwargs_check(b, {"replicas_delta": "many"})
    assert e.value.reason_code == "kwargs_invalid"


def test_kwargs_check_allowed_namespaces_ok():
    b = mk_binding(kwargs_constraints={"allowed_namespaces": ["payments", "ads"]})
    kwargs_check(b, {"namespace": "payments"})
    kwargs_check(b, {})  # absent namespace passes


def test_kwargs_check_allowed_namespaces_violation():
    b = mk_binding(kwargs_constraints={"allowed_namespaces": ["payments"]})
    with pytest.raises(DenyClosed) as e:
        kwargs_check(b, {"namespace": "kube-system"})
    assert e.value.reason_code == "kwargs_constraint_violation"


def test_kwargs_check_denied_args():
    b = mk_binding(kwargs_constraints={"denied_args": ["force"]})
    with pytest.raises(DenyClosed) as e:
        kwargs_check(b, {"force": True})
    assert e.value.reason_code == "kwargs_constraint_violation"


def test_kwargs_check_max_string_length():
    b = mk_binding(kwargs_constraints={"max_string_length": 5})
    with pytest.raises(DenyClosed) as e:
        kwargs_check(b, {"comment": "way too long"})
    assert e.value.reason_code == "kwargs_constraint_violation"


def test_kwargs_check_unknown_constraint_ignored():
    b = mk_binding(kwargs_constraints={"futuristic_constraint": True})
    kwargs_check(b, {"x": 1})


def test_kwargs_check_replicas_delta_absent_passes():
    b = mk_binding(kwargs_constraints={"max_replicas_delta": 2})
    kwargs_check(b, {"deployment": "x"})


# ---------------- budget_check ----------------


def test_budget_check_ok_within_cap():
    st = BudgetState(tokens_used=100, usd_micros_used=0, tokens_cap=1000, usd_micros_cap=10_000)
    budget_check(st, 200, 5_000)


def test_budget_check_tokens_exceeded():
    st = BudgetState(tokens_used=900, usd_micros_used=0, tokens_cap=1000, usd_micros_cap=10_000)
    with pytest.raises(BudgetExceeded):
        budget_check(st, 200, 0)


def test_budget_check_usd_exceeded():
    st = BudgetState(tokens_used=0, usd_micros_used=9_000, tokens_cap=1000, usd_micros_cap=10_000)
    with pytest.raises(BudgetExceeded):
        budget_check(st, 0, 2_000)


def test_budget_check_negative_estimate():
    st = BudgetState(tokens_used=0, usd_micros_used=0, tokens_cap=1000, usd_micros_cap=10_000)
    with pytest.raises(DenyClosed) as e:
        budget_check(st, -1, 0)
    assert e.value.reason_code == "budget_invalid_estimate"


def test_budget_check_zero_cap_means_unlimited():
    # cap=0 -> disabled
    st = BudgetState(tokens_used=10**9, usd_micros_used=10**9, tokens_cap=0, usd_micros_cap=0)
    budget_check(st, 1, 1)
