"""Tests for slo/burnrate.py — multi-window burn math + alert classification."""

from __future__ import annotations

import pytest

from acp.clock import FrozenClock
from acp.events.query import EventQuery
from acp.events.store import WideEventStore
from acp.schemas.slo import BurnRateWindow
from acp.slo.burnrate import (
    BURN_WINDOWS,
    burn_alert_level,
    burn_rate,
    multi_window_burn_rates,
    window_seconds,
)


AGENT = "oncall"
TASK = "triage"
MODEL = "claude-sonnet-4.6@2026-05-01"


# -- pure math ------------------------------------------------------------


def test_burn_rate_basic_sli_05_target_095():
    # 1 - sli = 0.5; 1 - target = 0.05; burn = 10.0
    assert burn_rate(0.5, 0.95) == pytest.approx(10.0)


def test_burn_rate_perfect_sli_is_zero():
    assert burn_rate(1.0, 0.95) == 0.0


def test_burn_rate_at_target_is_one():
    assert burn_rate(0.95, 0.95) == pytest.approx(1.0)


def test_burn_rate_target_one_caps_high():
    # No budget at all; any miss → "infinite" burn.
    assert burn_rate(0.0, 1.0) > 1e8
    assert burn_rate(1.0, 1.0) == 0.0


def test_burn_rate_rejects_negative_target():
    with pytest.raises(ValueError):
        burn_rate(0.5, -0.1)


# -- alert level classification ------------------------------------------


def _br(label: str, br: float) -> BurnRateWindow:
    return BurnRateWindow(
        label=label,  # type: ignore[arg-type]
        window_seconds=window_seconds(label),  # type: ignore[arg-type]
        sli_value=0.0,
        target=0.95,
        burn_rate=br,
    )


def test_alert_level_ok():
    assert burn_alert_level({"1h": _br("1h", 1.0), "6h": _br("6h", 1.0)}) == "ok"


def test_alert_level_warn_when_1h_over_6x():
    assert burn_alert_level({"1h": _br("1h", 7.0), "6h": _br("6h", 1.0)}) == "warn"


def test_alert_level_warn_when_6h_over_3x():
    assert burn_alert_level({"1h": _br("1h", 1.0), "6h": _br("6h", 5.0)}) == "warn"


def test_alert_level_critical_when_1h_over_14_4x():
    assert (
        burn_alert_level({"1h": _br("1h", 15.0), "6h": _br("6h", 1.0)}) == "critical"
    )


def test_alert_level_exhausted_takes_priority():
    # Even with 1h burning critical, 7d>1 means budget already gone → exhausted.
    levels = {
        "1h": _br("1h", 20.0),
        "6h": _br("6h", 1.0),
        "24h": _br("24h", 1.0),
        "7d": _br("7d", 1.5),
    }
    assert burn_alert_level(levels) == "exhausted"


# -- multi_window_burn_rates over real EventQuery -----------------------


def test_multi_window_burn_rates_judge_pass(tmp_db, frozen_clock: FrozenClock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    # 100 judgments, 50 pass, 50 fail → sli = 0.5; target = 0.95 → burn = 10.
    for i in range(100):
        store.emit(
            run_id=f"r{i}", agent_id=AGENT, task_class=TASK, model_version=MODEL,
            step=0, event_type="judgment",
            attrs={"verdict": "pass" if i < 50 else "fail"},
        )

    rates = multi_window_burn_rates(
        EventQuery(tmp_db), AGENT, TASK, MODEL,
        "judge_pass_rate", 0.95, frozen_clock,
    )
    assert set(rates.keys()) == {label for label, _ in BURN_WINDOWS}
    # 1h window includes everything; burn ≈ 10.
    assert rates["1h"].burn_rate == pytest.approx(10.0, rel=1e-3)
    assert rates["1h"].sli_value == pytest.approx(0.5)
    # Alert: 1h burn 10; 7d burn also 10 (same events fall into all windows) →
    # since 7d > 1 means the long-window budget is gone too → exhausted.
    assert burn_alert_level(rates) == "exhausted"


def test_multi_window_burn_rates_critical_classification(tmp_db, frozen_clock):
    store = WideEventStore(tmp_db, clock=frozen_clock)
    # SLI = 0.25 → burn = 0.75 / 0.05 = 15 → critical.
    for i in range(100):
        store.emit(
            run_id=f"r{i}", agent_id=AGENT, task_class=TASK, model_version=MODEL,
            step=0, event_type="judgment",
            attrs={"verdict": "pass" if i < 25 else "fail"},
        )
    rates = multi_window_burn_rates(
        EventQuery(tmp_db), AGENT, TASK, MODEL,
        "judge_pass_rate", 0.95, frozen_clock,
    )
    # Same SLI across all windows; 7d > 1 → exhausted wins over critical.
    assert burn_alert_level(rates) == "exhausted"
    # But the 1h window indeed reports a critical-grade burn rate.
    assert rates["1h"].burn_rate > 14.4
