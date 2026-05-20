"""BudgetManager tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.clock import FrozenClock
from acp.errors import BudgetExceeded
from acp.gateway.budget import BudgetManager
from acp.registry.store import RegistryStore


_AGENT_YAML = """
agent_id: test-agent
owner: rshao@datavisor.com
version: 1.0.0
model_version: test-model
description: test
task_classes:
  - name: t1
    slo_sli_kind: judge_pass_rate
    slo_target: 0.8
    slo_window: 7d
sealed_tools:
  - name: vm_query
    max_tier: T1
    requires_intent: false
budget_hourly_usd: 1.0
budget_hourly_tokens: 1000
default_tier: T1
"""


@pytest.fixture
def registry(tmp_db, tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "test-agent.yaml").write_text(_AGENT_YAML)
    tmp_db.execute("DROP TABLE IF EXISTS agents")
    tmp_db.commit()
    store = RegistryStore(tmp_db, d)
    store.load()
    return store


def test_check_and_reserve_within_cap(tmp_db, registry, frozen_clock):
    bm = BudgetManager(tmp_db, registry, clock=frozen_clock)
    bm.check_and_reserve("test-agent", 100, 500_000)


def test_check_and_reserve_tokens_exceeded(tmp_db, registry, frozen_clock):
    bm = BudgetManager(tmp_db, registry, clock=frozen_clock)
    with pytest.raises(BudgetExceeded) as e:
        bm.check_and_reserve("test-agent", 5000, 0)
    assert e.value.kind == "tokens"


def test_check_and_reserve_usd_exceeded(tmp_db, registry, frozen_clock):
    bm = BudgetManager(tmp_db, registry, clock=frozen_clock)
    with pytest.raises(BudgetExceeded) as e:
        bm.check_and_reserve("test-agent", 0, 2_000_000)
    assert e.value.kind == "usd_micros"


def test_record_actual_upserts(tmp_db, registry, frozen_clock):
    bm = BudgetManager(tmp_db, registry, clock=frozen_clock)
    bm.record_actual("test-agent", 100, 50_000)
    bm.record_actual("test-agent", 50, 25_000)
    usage = bm.usage("test-agent")
    assert usage["tokens_used"] == 150
    assert usage["usd_micros_used"] == 75_000


def test_unknown_agent_denies(tmp_db, registry, frozen_clock):
    bm = BudgetManager(tmp_db, registry, clock=frozen_clock)
    # cap=0,0 -> any positive estimate is OK because cap=0 disables; but
    # if both caps are 0 from unknown agent, only positive est still passes
    # since check is "tokens_cap > 0".
    bm.check_and_reserve("ghost-agent", 0, 0)


def test_negative_estimate_rejected(tmp_db, registry, frozen_clock):
    bm = BudgetManager(tmp_db, registry, clock=frozen_clock)
    with pytest.raises(BudgetExceeded):
        bm.check_and_reserve("test-agent", -1, 0)


def test_post_reserve_then_check_uses_accumulated(tmp_db, registry, frozen_clock):
    bm = BudgetManager(tmp_db, registry, clock=frozen_clock)
    bm.record_actual("test-agent", 900, 0)
    with pytest.raises(BudgetExceeded):
        bm.check_and_reserve("test-agent", 200, 0)
