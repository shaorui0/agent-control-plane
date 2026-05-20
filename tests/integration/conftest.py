"""Shared integration fixtures — in-memory ACP gateway with the demo agent yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI

from acp.events.store import WideEventStore
from acp.gateway.auth import SessionAuth
from acp.gateway.budget import BudgetManager
from acp.gateway.idempotency import IdempotencyVault
from acp.gateway.routes import DefaultAutonomyProvider, GatewayDeps, build_router
from acp.gateway.tools import REGISTRY as _REGISTRY  # noqa: F401 — register tools
from acp.registry.store import RegistryStore
from acp.sdk.client import LocalClient
from acp.schemas.agent import AutonomyTier


DEMO_AGENT_YAML = """
agent_id: demo-oncall
owner: rshao@datavisor.com
version: 1.0.0
model_version: claude-sonnet-4-7
description: Demo on-call triage agent.
task_classes:
  - {name: triage, slo_sli_kind: judge_pass_rate, slo_target: 0.8, slo_window: 7d}
sealed_tools:
  - {name: vm_query, max_tier: T1, requires_intent: false}
  - {name: loki_query, max_tier: T1, requires_intent: false}
  - {name: kubectl_get, max_tier: T1, requires_intent: false}
  - {name: kubectl_describe, max_tier: T1, requires_intent: false}
  - {name: slack_post, max_tier: T2, requires_intent: false}
  - name: kubectl_scale
    max_tier: T3
    requires_intent: true
    kwargs_constraints: {max_replicas_delta: 2}
budget_hourly_usd: 5.0
budget_hourly_tokens: 100000
default_tier: T2
"""


class FixedTier:
    """Force a specific autonomy tier — used to bypass W4C controller."""

    def __init__(self, tier: AutonomyTier) -> None:
        self._t = tier

    def current_tier(self, agent_id: str, task_class: str) -> AutonomyTier:
        return self._t


@pytest.fixture
def acp_app(tmp_db, tmp_path: Path, frozen_clock):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "demo-oncall.yaml").write_text(DEMO_AGENT_YAML)
    tmp_db.execute("DROP TABLE IF EXISTS agents")
    tmp_db.commit()
    registry = RegistryStore(tmp_db, d)
    registry.load()

    events = WideEventStore(tmp_db, clock=frozen_clock)
    auth = SessionAuth()
    budget = BudgetManager(tmp_db, registry, clock=frozen_clock)
    idem = IdempotencyVault()

    from acp.gateway.tools.base import REGISTRY as TOOL_REG
    deps = GatewayDeps(
        conn=tmp_db, registry=registry, events=events, auth=auth,
        budget=budget, idempotency=idem, tools=TOOL_REG,
        # Default to T3 so kubectl_scale path can be exercised.
        autonomy=FixedTier(AutonomyTier.T3),
    )
    app = FastAPI()
    app.include_router(build_router(deps))
    return app, deps


@pytest.fixture
def local_client(acp_app) -> LocalClient:
    app, _ = acp_app
    return LocalClient(app)
