"""CLI tests via Typer's CliRunner.

Each subcommand is invoked against a tmp_path-scoped DB + registry so they
don't share state.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from acp.cli import app as cli_app
from acp.db import connect, migrate
from acp.ids import new_ulid
from acp.settings import get_settings


_AGENT_YAML = """
agent_id: oncall-agent
owner: rshao@datavisor.com
version: 1.0.0
model_version: claude-sonnet-4-7
description: test
task_classes:
  - {name: triage, slo_sli_kind: judge_pass_rate, slo_target: 0.8, slo_window: 7d}
sealed_tools:
  - {name: vm_query, max_tier: T1, requires_intent: false}
budget_hourly_usd: 5.0
budget_hourly_tokens: 100000
default_tier: T1
"""


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch):
    """Configure ACP env to point at tmp_path, and reset the settings cache."""
    db_path = tmp_path / "acp.db"
    registry_dir = tmp_path / "agents"
    registry_dir.mkdir()
    monkeypatch.setenv("ACP_DB_PATH", str(db_path))
    monkeypatch.setenv("ACP_REGISTRY_DIR", str(registry_dir))
    # Clear lru_cache.
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield db_path, registry_dir
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _runner() -> CliRunner:
    # mix_stderr default avoids deprecation warning across Click versions.
    return CliRunner()


def test_register_validates_and_copies(cli_env, tmp_path: Path):
    _, registry_dir = cli_env
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(_AGENT_YAML)
    runner = _runner()
    result = runner.invoke(cli_app, ["register", "--file", str(spec_file)])
    assert result.exit_code == 0, result.output
    assert (registry_dir / "spec.yaml").exists()
    assert "oncall-agent" in result.output


def test_register_rejects_invalid_yaml(cli_env, tmp_path: Path):
    spec_file = tmp_path / "bad.yaml"
    spec_file.write_text("not: a: valid: spec")
    runner = _runner()
    result = runner.invoke(cli_app, ["register", "--file", str(spec_file)])
    assert result.exit_code != 0


def test_verify_empty_db(cli_env):
    db_path, _ = cli_env
    conn = connect(db_path)
    migrate(conn)
    conn.close()

    runner = _runner()
    result = runner.invoke(cli_app, ["verify", "--db", str(db_path)])
    assert result.exit_code == 0
    assert "no runs to verify" in result.output


def test_slo_no_snapshot_exits_nonzero(cli_env):
    db_path, _ = cli_env
    conn = connect(db_path)
    migrate(conn)
    conn.close()

    runner = _runner()
    result = runner.invoke(
        cli_app, ["slo", "--agent", "ghost", "--task-class", "x", "--window", "1h"]
    )
    assert result.exit_code != 0


def test_burn_no_snapshot_exits_nonzero(cli_env):
    db_path, _ = cli_env
    conn = connect(db_path)
    migrate(conn)
    conn.close()

    runner = _runner()
    result = runner.invoke(cli_app, ["burn", "--agent", "ghost"])
    assert result.exit_code != 0


def test_slo_pretty_prints_latest(cli_env):
    db_path, _ = cli_env
    conn = connect(db_path)
    migrate(conn)
    conn.execute(
        "INSERT INTO slo_snapshots (snapshot_id, ts, agent_id, task_class, model_version, "
        "window_label, budget_class, sli_value, slo_target, burn_rate, budget_remaining) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            new_ulid(),
            1_000_000,
            "oncall-agent",
            "triage",
            "claude-sonnet-4-7",
            "7d",
            "organic",
            0.9,
            0.8,
            1.5,
            0.7,
        ),
    )
    conn.commit()
    conn.close()

    runner = _runner()
    result = runner.invoke(
        cli_app, ["slo", "--agent", "oncall-agent", "--task-class", "triage", "--window", "7d"]
    )
    assert result.exit_code == 0
    assert "oncall-agent" in result.output
    assert "7d" in result.output


def test_audit_list_empty(cli_env):
    db_path, _ = cli_env
    conn = connect(db_path)
    migrate(conn)
    conn.close()
    runner = _runner()
    result = runner.invoke(cli_app, ["audit", "list"])
    assert result.exit_code == 0
    assert "no pending audits" in result.output


def test_audit_decide_unknown_returns_nonzero(cli_env):
    db_path, _ = cli_env
    conn = connect(db_path)
    migrate(conn)
    conn.close()
    runner = _runner()
    result = runner.invoke(
        cli_app,
        ["audit", "decide", "ghost", "--human-label", "pass", "--reviewer", "alice@x"],
    )
    assert result.exit_code != 0


def test_approve_unknown_returns_nonzero(cli_env):
    db_path, _ = cli_env
    conn = connect(db_path)
    migrate(conn)
    conn.close()
    runner = _runner()
    result = runner.invoke(
        cli_app, ["approve", "ghost", "--reviewer", "alice@x"]
    )
    assert result.exit_code != 0


def test_autonomy_promote_requires_operator_signature(cli_env, tmp_path: Path):
    """Operator must be a non-empty string; missing operator fails at CLI parse."""
    db_path, registry_dir = cli_env
    spec_file = registry_dir / "oncall.yaml"
    spec_file.write_text(_AGENT_YAML)

    runner = _runner()
    # Missing --operator -> typer Exit.
    result = runner.invoke(
        cli_app, ["autonomy", "promote", "oncall-agent", "triage", "--tier", "T2"]
    )
    assert result.exit_code != 0


def test_autonomy_promote_happy_path(cli_env):
    db_path, registry_dir = cli_env
    (registry_dir / "oncall.yaml").write_text(_AGENT_YAML)

    runner = _runner()
    result = runner.invoke(
        cli_app,
        [
            "autonomy",
            "promote",
            "oncall-agent",
            "triage",
            "--tier",
            "T2",
            "--operator",
            "rshao@datavisor.com",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "T2" in result.output


def test_dashboard_command_prints_url(cli_env):
    runner = _runner()
    result = runner.invoke(cli_app, ["dashboard", "--port", "9999"])
    assert result.exit_code == 0
    assert "9999" in result.output
