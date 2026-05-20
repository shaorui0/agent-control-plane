"""Demo on-call agent — uses LocalClient (in-process, no HTTP).

The `think()` function is a FAKE LLM: deterministic canned responses driven by
the scenario JSON. NOT a real LLM — this exists to showcase the ACP wiring
end-to-end without needing API keys.

Usage:
    python demo/oncall_agent.py --scenario 01 [--db /tmp/acp.db]

Exit code: 0 if scenario completed as expected, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI

from acp.clock import FrozenClock
from acp.db import connect, migrate
from acp.errors import DenyClosed
from acp.events.query import EventQuery
from acp.events.store import WideEventStore
from acp.gateway.auth import SessionAuth
from acp.gateway.budget import BudgetManager
from acp.gateway.idempotency import IdempotencyVault
from acp.gateway.routes import GatewayDeps, build_router
from acp.gateway.tools import REGISTRY as _TOOL_REGISTRY  # noqa: F401 — register tools
from acp.judge.adversarial import CoTAdversarialJudge
from acp.judge.goodhart import detect_all, persist_flags
from acp.judge.llm_clients import StubJudge
from acp.judge.pipeline import JudgePipeline
from acp.registry.store import RegistryStore
from acp.schemas.agent import AutonomyTier
from acp.schemas.decision import AgentDecision
from acp.schemas.outcome import OutcomeSignal
from acp.schemas.tool import ToolCallRequest
from acp.sdk.client import LocalClient
from acp.slo.feedback import ingest_outcome_signal, maybe_flip_verdict


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
budget_hourly_tokens: 500000
default_tier: T2
"""


class _FixedTier:
    def __init__(self, tier: AutonomyTier) -> None:
        self._t = tier

    def current_tier(self, agent_id: str, task_class: str) -> AutonomyTier:
        return self._t


def build_acp(db_path: Path):
    conn = connect(db_path)
    migrate(conn)
    agents_dir = db_path.parent / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "demo-oncall.yaml").write_text(DEMO_AGENT_YAML)
    conn.execute("DROP TABLE IF EXISTS agents")
    conn.commit()
    registry = RegistryStore(conn, agents_dir)
    registry.load()
    clock = FrozenClock(at_ms=1_767_225_600_000)
    deps = GatewayDeps(
        conn=conn, registry=registry,
        events=WideEventStore(conn, clock=clock),
        auth=SessionAuth(),
        budget=BudgetManager(conn, registry, clock=clock),
        idempotency=IdempotencyVault(),
        tools=_TOOL_REGISTRY,
        autonomy=_FixedTier(AutonomyTier.T3),
    )
    app = FastAPI()
    app.include_router(build_router(deps))
    return app, deps, clock


SCENARIO_DIR = Path(__file__).parent / "scenarios"
EXPECTED_PATH = Path(__file__).parent / "expected_outcomes.json"


def load_scenario(scenario_id: str) -> dict:
    """Find the scenario file by prefix match (e.g. '01' -> '01_cpu_spike.json')."""
    for p in SCENARIO_DIR.iterdir():
        if p.name.startswith(scenario_id):
            return json.loads(p.read_text())
    raise FileNotFoundError(f"no scenario for {scenario_id!r}")


async def fake_think(scenario_step: dict) -> dict:
    """FAKE-LLM-FOR-DEMO. Deterministic encoded responses. NOT a real model.

    Returns a dict shaped like an LLM's structured output.
    """
    return dict(scenario_step)


async def run_scenario(scenario_id: str, db_path: Path) -> dict:
    scenario = load_scenario(scenario_id)
    app, deps, clock = build_acp(db_path)
    client = LocalClient(app)

    sess = await client.start_session("demo-oncall", "triage",
                                      {"alert": scenario.get("alert", "")})
    run_id = sess["run_id"]

    tool_calls = 0
    denials = 0
    goodhart_flag_count = 0

    for step in scenario["agent_script"]:
        decision = await fake_think(step)

        # Goodhart probe scenario: inflate reasoning + name-drop a different tool.
        if decision.get("long_reasoning"):
            reasoning = "I need to carefully scale the deployment. " * 200
            name_drop = decision.get("name_drop_tool", "kubectl_scale")
            full = f"{reasoning} I should call {name_drop}() to fix this."
            # Build AgentDecision for the goodhart detector.
            from acp.ids import new_ulid
            ad = AgentDecision(
                prompt_hash="demo",
                reasoning=full,
                chosen_action=ToolCallRequest(
                    tool_name=decision["tool"], args=decision.get("args", {}),
                    intent=decision.get("intent", "x"),
                    run_id=run_id, idempotency_key=new_ulid(),
                ),
                self_confidence=0.5, tokens_in=100, tokens_out=2000,
            )
            flags = detect_all(ad, prior_events=[], baseline_p95_chars=400.0)
            goodhart_flag_count += len(flags)
            persist_flags(deps.conn, "demo-event", flags, clock.now())

        await client.post_decision(
            run_id, intent=decision.get("intent", ""),
            rationale=decision.get("rationale", ""),
            chosen_tool=decision.get("tool"),
            chosen_args=decision.get("args", {}),
        )

        if decision["action"] == "tool":
            try:
                resp = await client.invoke_tool(
                    run_id, decision["tool"], decision.get("args", {}),
                    intent=decision.get("intent", ""),
                    agent_claim=decision.get("rationale"),
                )
                if resp.get("status") == "pending_approval":
                    # Auto-grant T3 for demo (operator stand-in).
                    deps.conn.execute(
                        "UPDATE approvals SET status='granted', decided_by='demo-operator', "
                        "decided_at=? WHERE approval_id=?",
                        (clock.now(), resp["approval_id"]),
                    )
                    deps.conn.commit()
                tool_calls += 1
            except DenyClosed as e:
                denials += 1
                print(f"  [{scenario_id}] denied: {e.reason_code}")
        elif decision["action"] == "final":
            break

    await client.end_session(
        run_id, final_output={"answer": "done"},
        agent_claim_outcome=scenario.get("expected", {}).get("agent_claim", "ok"),
    )

    # Judge.
    q = EventQuery(deps.conn)
    judgment_verdict = None
    retroactive_flip = False
    if tool_calls > 0:
        try:
            pipeline = JudgePipeline(
                event_store=deps.events, query=q, registry_store=deps.registry,
                judges=[StubJudge("A"), StubJudge("B")],
            )
            panel = await pipeline.judge_task(run_id)
            judgment_verdict = panel.final_label
        except Exception as e:
            print(f"  [{scenario_id}] judge skipped: {type(e).__name__}: {e}")

    # Post-action: ingest outcome signal (scenario 02).
    for post in scenario.get("post_actions", []):
        if post["action"] == "post_outcome":
            later = FrozenClock(at_ms=clock.now() + post["delay_seconds"] * 1000)
            signal = OutcomeSignal(
                signal_id=f"sig-{scenario_id}-1", run_id=run_id, kind=post["kind"],
                value_json={}, delay_seconds=post["delay_seconds"],
                source="demo", ts=later.now(),
            )
            ingest_outcome_signal(signal, deps.conn)
            retroactive_flip = maybe_flip_verdict(run_id, deps.conn, later)

    return {
        "scenario_id": scenario["scenario_id"],
        "run_id": run_id,
        "tool_calls": tool_calls,
        "denials": denials,
        "judgment_verdict": judgment_verdict,
        "retroactive_flip": retroactive_flip,
        "goodhart_flags": goodhart_flag_count,
    }


def verify(scenario_id: str, result: dict) -> tuple[bool, list[str]]:
    expected = json.loads(EXPECTED_PATH.read_text())[scenario_id]
    fails: list[str] = []
    if "min_tool_calls" in expected and result["tool_calls"] < expected["min_tool_calls"]:
        fails.append(f"tool_calls {result['tool_calls']} < min {expected['min_tool_calls']}")
    if "max_denials" in expected and result["denials"] > expected["max_denials"]:
        fails.append(f"denials {result['denials']} > max {expected['max_denials']}")
    if "min_denials" in expected and result["denials"] < expected["min_denials"]:
        fails.append(f"denials {result['denials']} < min {expected['min_denials']}")
    if expected.get("expect_retroactive_flip") and not result["retroactive_flip"]:
        fails.append("expected retroactive_flip but did not happen")
    if expected.get("expect_goodhart_flags", 0) > result["goodhart_flags"]:
        fails.append(
            f"goodhart_flags {result['goodhart_flags']} < "
            f"expected {expected['expect_goodhart_flags']}"
        )
    return (len(fails) == 0), fails


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", required=True, help="e.g. 01, 02, 03, 04")
    p.add_argument("--db", default=None)
    args = p.parse_args(argv)

    db_path = Path(args.db) if args.db else Path(tempfile.mkdtemp()) / "acp_demo.db"
    result = asyncio.run(run_scenario(args.scenario, db_path))

    # Map "01" -> "01_cpu_spike" key for expected_outcomes.
    expected_key = result["scenario_id"]
    ok, fails = verify(expected_key, result)

    print(json.dumps({"result": result, "ok": ok, "fails": fails}, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
