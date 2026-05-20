"""Error class invariants — no internal-state leak in agent serialization."""
from __future__ import annotations

import pytest

from acp.errors import ACPError, BudgetExceeded, DenyClosed, IntegrityError


def test_deny_closed_carries_reason_code():
    e = DenyClosed("tool_not_in_tier", "agent tier=T0, tool requires T2; agent_id=a1")
    assert e.reason_code == "tool_not_in_tier"
    assert "a1" in e.message  # internal message can carry detail


def test_deny_closed_agent_dict_hides_internal_message():
    e = DenyClosed("budget_exhausted", "USD micros=1234567 over cap 1000000 for agent foo")
    d = e.to_agent_dict()
    assert d == {"status": "denied", "reason_code": "budget_exhausted"}
    # Internal detail MUST NOT appear in the agent-facing dict.
    for v in d.values():
        assert "foo" not in v
        assert "1234567" not in v


def test_deny_closed_default_message():
    e = DenyClosed("intent_missing")
    assert e.reason_code == "intent_missing"


def test_integrity_error_is_acp_error():
    with pytest.raises(ACPError):
        raise IntegrityError("chain broken at event 42")


def test_budget_exceeded_fields():
    e = BudgetExceeded("tokens", limit=1000.0, observed=1500.0)
    assert e.kind == "tokens"
    assert e.limit == 1000.0
    assert e.observed == 1500.0
    assert "exceeded" in str(e)
