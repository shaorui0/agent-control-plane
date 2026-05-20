"""Tests for slo/engine.py + slo/alerts.py + slo/definitions.py."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from acp.clock import FrozenClock
from acp.events.query import EventQuery
from acp.events.store import WideEventStore
from acp.registry.store import RegistryStore
from acp.schemas.slo import BudgetSnapshot
from acp.slo.alerts import AlertRouter, AlertSink, FileSink, StdoutSink
from acp.slo.definitions import SLODefinitionRegistry, parse_window
from acp.slo.engine import SLOEngine


AGENT = "oncall-triage-agent"
MODEL = "claude-sonnet-4-7"


# -- definitions / parse_window ------------------------------------------


def test_parse_window_variants():
    assert parse_window("1h") == 3600
    assert parse_window("6h") == 6 * 3600
    assert parse_window("24h") == 24 * 3600
    assert parse_window("7d") == 7 * 86_400
    assert parse_window("30d") == 30 * 86_400
    assert parse_window("60s") == 60


def test_parse_window_rejects_bad_spec():
    with pytest.raises(ValueError):
        parse_window("forever")


def _agents_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "agents"


def test_definition_registry_extracts_from_yaml(tmp_db):
    store = RegistryStore(sqlite3.connect(":memory:"), _agents_dir())
    store.load()
    reg = SLODefinitionRegistry(store)

    defs = reg.all_definitions(include_adversarial=False)
    # 2 task classes on oncall, at least 1 on code_reviewer (if present).
    triage_defs = [d for d in defs if d.task_class == "triage_alert"]
    assert len(triage_defs) == 1
    d = triage_defs[0]
    assert d.agent_id == AGENT
    assert d.model_version == "claude-sonnet-4-7"
    assert d.target == 0.75
    assert d.window_seconds == 7 * 86_400
    assert d.budget_class == "organic"


def test_definition_registry_includes_adversarial_twin(tmp_db):
    store = RegistryStore(sqlite3.connect(":memory:"), _agents_dir())
    store.load()
    reg = SLODefinitionRegistry(store)
    defs = reg.all_definitions(include_adversarial=True)
    organic = [d for d in defs if d.budget_class == "organic"]
    adversarial = [d for d in defs if d.budget_class == "adversarial"]
    assert len(adversarial) == len(organic)


# -- engine evaluate_all -------------------------------------------------


def test_engine_evaluate_all_writes_snapshots(tmp_db, frozen_clock: FrozenClock):
    rstore = RegistryStore(sqlite3.connect(":memory:"), _agents_dir())
    rstore.load()
    defs = SLODefinitionRegistry(rstore)

    # Seed 100 judgments — 80 pass — for triage_alert.
    estore = WideEventStore(tmp_db, clock=frozen_clock)
    for i in range(100):
        estore.emit(
            run_id=f"r{i}", agent_id=AGENT, task_class="triage_alert",
            model_version=MODEL, step=0, event_type="judgment",
            attrs={"verdict": "pass" if i < 80 else "fail"},
        )

    engine = SLOEngine(tmp_db, EventQuery(tmp_db), rstore, defs, frozen_clock)
    snaps = engine.evaluate_all()

    triage_snaps = [
        s for s in snaps
        if s.agent_id == AGENT and s.task_class == "triage_alert"
    ]
    # 4 windows × 2 budget_classes = 8 rows for triage_alert.
    assert len(triage_snaps) == 8

    # Rows are persisted.
    persisted = tmp_db.execute(
        "SELECT COUNT(*) FROM slo_snapshots WHERE task_class='triage_alert'"
    ).fetchone()[0]
    assert persisted == 8

    # SLI value matches the seed (judge_pass_rate = 0.8).
    organic_1h = [
        s for s in triage_snaps
        if s.window_label == "1h" and s.budget_class == "organic"
    ][0]
    assert organic_1h.sli_value == pytest.approx(0.8)


# -- alert router rate-limit --------------------------------------------


class _RecordingSink(AlertSink):
    def __init__(self) -> None:
        self.records: list[tuple[str, BudgetSnapshot, str]] = []

    def emit_alert(self, level: str, snapshot: BudgetSnapshot, message: str) -> None:
        self.records.append((level, snapshot, message))


def _snap(burn: float, label: str = "1h", agent_id: str = "a", task_class: str = "t") -> BudgetSnapshot:
    return BudgetSnapshot(
        snapshot_id="sn1",
        ts=1_000_000,
        agent_id=agent_id,
        task_class=task_class,
        model_version=MODEL,
        window_label=label,  # type: ignore[arg-type]
        budget_class="organic",
        sli_value=0.5,
        slo_target=0.95,
        burn_rate=burn,
        budget_remaining=0.0,
    )


def test_alert_router_rate_limits_within_an_hour():
    clk = FrozenClock(at_ms=1_000_000_000)
    sink = _RecordingSink()
    router = AlertRouter(sink, clock=clk)

    s1 = _snap(15.0)
    assert router.evaluate("critical", s1) is True
    # 5 minutes later — suppressed.
    clk.advance(5 * 60 * 1000)
    s2 = _snap(15.0)
    assert router.evaluate("critical", s2) is False
    assert len(sink.records) == 1


def test_alert_router_emits_after_rate_limit_window():
    clk = FrozenClock(at_ms=1_000_000_000)
    sink = _RecordingSink()
    router = AlertRouter(sink, clock=clk)

    assert router.evaluate("critical", _snap(15.0)) is True
    clk.advance(61 * 60 * 1000)  # > 1h
    assert router.evaluate("critical", _snap(15.0)) is True
    assert len(sink.records) == 2


def test_alert_router_separate_levels_not_rate_limited_together():
    clk = FrozenClock(at_ms=1_000_000_000)
    sink = _RecordingSink()
    router = AlertRouter(sink, clock=clk)
    assert router.evaluate("warn", _snap(7.0)) is True
    assert router.evaluate("critical", _snap(15.0)) is True
    assert len(sink.records) == 2


def test_alert_router_ok_clears_rate_limit():
    clk = FrozenClock(at_ms=1_000_000_000)
    sink = _RecordingSink()
    router = AlertRouter(sink, clock=clk)

    assert router.evaluate("critical", _snap(15.0)) is True
    # Recovered: ok clears the dedup so the next burn alerts immediately.
    router.evaluate("ok", _snap(0.1))
    clk.advance(60 * 1000)  # only 1 minute later
    assert router.evaluate("critical", _snap(15.0)) is True


def test_stdout_sink_does_not_crash(capsys):
    StdoutSink().emit_alert("warn", _snap(7.0), "hello")
    out = capsys.readouterr().out
    assert "ACP-ALERT" in out and "warn" in out


def test_file_sink_appends_jsonl(tmp_path):
    p = tmp_path / "alerts.log"
    sink = FileSink(p)
    sink.emit_alert("critical", _snap(15.0), "danger")
    sink.emit_alert("warn", _snap(7.0), "watch")
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 2
    assert "critical" in lines[0] and "warn" in lines[1]
