"""Unit tests for L0 registry: loader + validator + RegistryStore."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from acp.registry import (
    RegistryLoadError,
    RegistryStore,
    RegistryValidationError,
    load_dir,
    validate,
)
from acp.schemas.agent import (
    AgentSpec,
    AutonomyTier,
    TaskClassConfig,
    ToolBinding,
)


# ----- fixtures ------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"


def _good_spec(**overrides) -> AgentSpec:
    base = dict(
        agent_id="test-agent",
        owner="rshao@datavisor.com",
        version="1.0.0",
        model_version="claude-sonnet-4-7",
        description="test",
        task_classes=[
            TaskClassConfig(
                name="t",
                slo_sli_kind="judge_pass_rate",
                slo_target=0.8,
                slo_window="7d",
            )
        ],
        sealed_tools=[
            ToolBinding(name="vm_query", max_tier=AutonomyTier.T1, requires_intent=False)
        ],
        budget_hourly_usd=1.0,
        budget_hourly_tokens=10_000,
        default_tier=AutonomyTier.T1,
    )
    base.update(overrides)
    return AgentSpec(**base)


def _write_yaml(dirpath: Path, name: str, body: str) -> Path:
    p = dirpath / f"{name}.yaml"
    p.write_text(body, encoding="utf-8")
    return p


# ----- happy path ----------------------------------------------------


def test_load_dir_real_agents():
    """The two shipped YAMLs must load and validate."""
    agents = load_dir(AGENTS_DIR)
    assert set(agents.keys()) == {"oncall-triage-agent", "code-reviewer-agent"}
    triage = agents["oncall-triage-agent"]
    assert triage.owner == "rshao@datavisor.com"
    assert triage.default_tier == AutonomyTier.T1
    scale_tool = next(t for t in triage.sealed_tools if t.name == "kubectl_scale")
    assert scale_tool.max_tier == AutonomyTier.T3
    assert scale_tool.requires_intent is True


# ----- validator rejects ---------------------------------------------


def test_validator_rejects_missing_owner(tmp_path: Path):
    body = """
agent_id: bad-owner
owner: ""
version: 1.0.0
model_version: m
task_classes:
  - {name: t, slo_sli_kind: judge_pass_rate, slo_target: 0.8, slo_window: 7d}
sealed_tools:
  - {name: vm_query, max_tier: T1, requires_intent: false}
budget_hourly_usd: 1.0
budget_hourly_tokens: 1000
default_tier: T1
"""
    _write_yaml(tmp_path, "bad", body)
    with pytest.raises(RegistryLoadError):
        # Pydantic owner validator catches empty/non-email; surfaces as RegistryLoadError.
        load_dir(tmp_path)


def test_validator_rejects_mutating_tool_below_t3():
    spec = _good_spec(
        sealed_tools=[
            ToolBinding(name="vm_query", max_tier=AutonomyTier.T1, requires_intent=False),
            ToolBinding(name="kubectl_scale", max_tier=AutonomyTier.T1, requires_intent=True),
        ]
    )
    errs = validate(spec)
    assert errs, "expected error for mutating tool below T3"
    assert any("kubectl_scale" in e and "T3" in e for e in errs)


def test_validator_rejects_default_tier_t4():
    spec = _good_spec(default_tier=AutonomyTier.T4)
    errs = validate(spec)
    assert any("default_tier" in e for e in errs)


def test_validator_rejects_default_tier_t3():
    spec = _good_spec(default_tier=AutonomyTier.T3)
    errs = validate(spec)
    assert any("default_tier" in e for e in errs)


def test_validator_rejects_zero_budget():
    spec = _good_spec(budget_hourly_usd=0.0)
    errs = validate(spec)
    assert any("budget_hourly_usd" in e for e in errs)


def test_validator_accepts_mutating_tool_at_t3():
    spec = _good_spec(
        sealed_tools=[
            ToolBinding(name="vm_query", max_tier=AutonomyTier.T1, requires_intent=False),
            ToolBinding(name="kubectl_scale", max_tier=AutonomyTier.T3, requires_intent=True),
        ]
    )
    assert validate(spec) == []


def test_validator_rejects_mutating_without_intent():
    spec = _good_spec(
        sealed_tools=[
            ToolBinding(name="vm_query", max_tier=AutonomyTier.T1, requires_intent=False),
            ToolBinding(name="kubectl_scale", max_tier=AutonomyTier.T3, requires_intent=False),
        ]
    )
    errs = validate(spec)
    assert any("requires_intent" in e for e in errs)


# ----- duplicate detection -------------------------------------------


def test_load_dir_rejects_duplicate_agent_id(tmp_path: Path):
    body = """
agent_id: dupe
owner: rshao@datavisor.com
version: 1.0.0
model_version: m
task_classes:
  - {name: t, slo_sli_kind: judge_pass_rate, slo_target: 0.8, slo_window: 7d}
sealed_tools:
  - {name: vm_query, max_tier: T1, requires_intent: false}
budget_hourly_usd: 1.0
budget_hourly_tokens: 1000
default_tier: T1
"""
    _write_yaml(tmp_path, "a", body)
    _write_yaml(tmp_path, "b", body)
    with pytest.raises(RegistryLoadError, match="duplicate"):
        load_dir(tmp_path)


def test_load_dir_rejects_t1_kubectl_scale_yaml(tmp_path: Path):
    body = """
agent_id: bad-tier
owner: rshao@datavisor.com
version: 1.0.0
model_version: m
task_classes:
  - {name: t, slo_sli_kind: judge_pass_rate, slo_target: 0.8, slo_window: 7d}
sealed_tools:
  - {name: kubectl_scale, max_tier: T1, requires_intent: true}
budget_hourly_usd: 1.0
budget_hourly_tokens: 1000
default_tier: T1
"""
    _write_yaml(tmp_path, "bad", body)
    with pytest.raises(RegistryValidationError):
        load_dir(tmp_path)


def test_load_dir_rejects_default_tier_t4_yaml(tmp_path: Path):
    body = """
agent_id: bad-default
owner: rshao@datavisor.com
version: 1.0.0
model_version: m
task_classes:
  - {name: t, slo_sli_kind: judge_pass_rate, slo_target: 0.8, slo_window: 7d}
sealed_tools:
  - {name: vm_query, max_tier: T1, requires_intent: false}
budget_hourly_usd: 1.0
budget_hourly_tokens: 1000
default_tier: T4
"""
    _write_yaml(tmp_path, "bad", body)
    with pytest.raises(RegistryValidationError):
        load_dir(tmp_path)


# ----- RegistryStore -------------------------------------------------


def _fresh_store(dir: Path) -> RegistryStore:
    conn = sqlite3.connect(":memory:")
    return RegistryStore(conn, dir)


def test_store_get_and_tool_binding():
    store = _fresh_store(AGENTS_DIR)
    store.load()

    spec = store.get("oncall-triage-agent")
    assert spec is not None
    assert spec.agent_id == "oncall-triage-agent"

    binding = store.get_tool_binding("oncall-triage-agent", "kubectl_scale")
    assert binding is not None
    assert binding.max_tier == AutonomyTier.T3
    assert binding.requires_intent is True

    assert store.get_tool_binding("oncall-triage-agent", "nope") is None
    assert store.get("nonexistent-agent") is None


def test_store_mirrors_to_sqlite():
    conn = sqlite3.connect(":memory:")
    store = RegistryStore(conn, AGENTS_DIR)
    store.load()
    rows = conn.execute("SELECT agent_id, owner, default_tier FROM agents").fetchall()
    assert len(rows) == 2
    by_id = {r[0]: r for r in rows}
    assert by_id["oncall-triage-agent"][1] == "rshao@datavisor.com"
    assert by_id["oncall-triage-agent"][2] == "T1"


def test_store_hot_reload(tmp_path: Path):
    # Seed dir with one agent.
    body_v1 = """
agent_id: hot-agent
owner: rshao@datavisor.com
version: 1.0.0
model_version: m
task_classes:
  - {name: t, slo_sli_kind: judge_pass_rate, slo_target: 0.8, slo_window: 7d}
sealed_tools:
  - {name: vm_query, max_tier: T1, requires_intent: false}
budget_hourly_usd: 1.0
budget_hourly_tokens: 1000
default_tier: T1
"""
    p = _write_yaml(tmp_path, "hot", body_v1)

    store = _fresh_store(tmp_path)
    store.load()
    assert store.get("hot-agent").version == "1.0.0"

    # Mutate file on disk: bump version + add a new tool.
    body_v2 = body_v1.replace("version: 1.0.0", "version: 2.0.0").replace(
        "sealed_tools:\n  - {name: vm_query, max_tier: T1, requires_intent: false}",
        "sealed_tools:\n  - {name: vm_query, max_tier: T1, requires_intent: false}\n"
        "  - {name: slack_post, max_tier: T2, requires_intent: false}",
    )
    # Ensure mtime advances (some filesystems have second-resolution mtime).
    time.sleep(0.01)
    p.write_text(body_v2, encoding="utf-8")

    store.reload()
    spec = store.get("hot-agent")
    assert spec.version == "2.0.0"
    assert store.get_tool_binding("hot-agent", "slack_post") is not None


def test_store_reload_drops_removed_agents(tmp_path: Path):
    body = """
agent_id: ephemeral
owner: rshao@datavisor.com
version: 1.0.0
model_version: m
task_classes:
  - {name: t, slo_sli_kind: judge_pass_rate, slo_target: 0.8, slo_window: 7d}
sealed_tools:
  - {name: vm_query, max_tier: T1, requires_intent: false}
budget_hourly_usd: 1.0
budget_hourly_tokens: 1000
default_tier: T1
"""
    p = _write_yaml(tmp_path, "eph", body)
    store = _fresh_store(tmp_path)
    store.load()
    assert store.get("ephemeral") is not None

    p.unlink()
    store.reload()
    assert store.get("ephemeral") is None
