"""Sandbox tests — Trajectory step cap + fanout."""

from __future__ import annotations

import asyncio

import pytest

from acp.errors import BudgetExceeded
from acp.sandbox.budgets import StepBudget
from acp.sandbox.fanout import parallel_subagents
from acp.sandbox.trajectory import Trajectory


def test_trajectory_step_cap():
    with Trajectory(run_id="R1", max_steps=3) as t:
        assert t.next_step() == 1
        assert t.next_step() == 2
        assert t.next_step() == 3
        with pytest.raises(BudgetExceeded) as e:
            t.next_step()
        assert e.value.kind == "max_steps"


def test_trajectory_default_cap_is_20():
    t = Trajectory(run_id="R")
    for i in range(20):
        assert t.next_step() == i + 1
    with pytest.raises(BudgetExceeded):
        t.next_step()


def test_step_budget():
    b = StepBudget(cap=2)
    b.consume()
    b.consume()
    assert b.remaining == 0
    with pytest.raises(BudgetExceeded):
        b.consume()


def test_parallel_subagents_joins_all():
    async def child(child_run_id: str, spec: dict) -> dict:
        await asyncio.sleep(0)
        return {"id": spec["id"]}

    results = asyncio.run(parallel_subagents("PARENT", [{"id": 1}, {"id": 2}, {"id": 3}], child))
    ids = sorted(r["id"] for r in results)
    assert ids == [1, 2, 3]
    for r in results:
        assert r["parent_run_id"] == "PARENT"
        assert "child_run_id" in r


def test_parallel_subagents_default_echo():
    results = asyncio.run(parallel_subagents("P", [{"a": 1}, {"b": 2}]))
    assert len(results) == 2
    assert results[0]["echo"] == {"a": 1}


def test_parallel_subagents_captures_child_errors():
    async def child(child_run_id: str, spec: dict) -> dict:
        if spec.get("boom"):
            raise RuntimeError("nope")
        return {"ok": True}

    results = asyncio.run(parallel_subagents("P", [{"boom": True}, {}], child))
    assert results[0]["error"] == "RuntimeError"
    assert results[1]["ok"] is True
