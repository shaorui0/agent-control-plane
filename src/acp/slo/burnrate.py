"""Burn-rate math — Google SRE Workbook multi-window-multi-burn-rate.

Burn rate is the ratio of *actual* error rate to *budgeted* error rate:

    burn = (1 - sli_value) / (1 - target)

burn > 1.0 → burning faster than the budget allows.
burn < 1.0 → consuming budget slower than allowed (saving budget).

Multi-window alerting (SRE workbook ch.5):
- critical: 1h burn > 14.4×  (≈ 2% of monthly budget in 1h)
- warn:     1h burn > 6× OR 6h burn > 3×
- exhausted: 7d burn > 1× (budget fully consumed)
"""

from __future__ import annotations

from typing import Callable

from acp.clock import Clock
from acp.events.query import EventQuery
from acp.events.sli import (
    intervention_free_rate,
    judge_pass_rate,
    p95_latency_ms,
    silent_fail_rate,
)
from acp.schemas.agent import SliKind
from acp.schemas.slo import BurnRateWindow, WindowLabel


# Canonical window definitions used by the SLOEngine + AlertRouter.
BURN_WINDOWS: list[tuple[WindowLabel, int]] = [
    ("1h", 3600),
    ("6h", 6 * 3600),
    ("24h", 24 * 3600),
    ("7d", 7 * 24 * 3600),
]


def window_seconds(label: WindowLabel) -> int:
    for lab, secs in BURN_WINDOWS:
        if lab == label:
            return secs
    raise ValueError(f"unknown window label {label!r}")


# -- core math -------------------------------------------------------------


def burn_rate(sli_value: float, target: float) -> float:
    """Return burn ratio. Caller has already chosen a target in [0, 1).

    By convention: target == 1.0 implies infinite burn for any miss; we clamp
    to a large finite value to keep downstream math well-behaved.
    """
    if target >= 1.0:
        return 0.0 if sli_value >= 1.0 else 1e9
    if target < 0.0:
        raise ValueError("target must be in [0, 1)")
    error_actual = max(0.0, 1.0 - sli_value)
    error_budget = 1.0 - target
    return error_actual / error_budget


# -- SLI dispatch ----------------------------------------------------------


SliFn = Callable[[EventQuery, str | None, str, str, int, Clock], float]


def _sli_fn(kind: SliKind) -> SliFn:
    mapping: dict[str, SliFn] = {
        "judge_pass_rate": judge_pass_rate,
        "intervention_free_rate": intervention_free_rate,
        "silent_fail_rate": silent_fail_rate,
        "p95_latency_ms": p95_latency_ms,
    }
    return mapping[kind]


def _sli_to_goodness(kind: SliKind, raw: float, target: float) -> float:
    """Coerce an SLI's "raw" value into a [0,1] "goodness" measure.

    For ratio SLIs (judge_pass_rate, intervention_free_rate) higher is better
    and the raw value already lives in [0,1].

    For silent_fail_rate, lower is better → goodness = 1 - rate.

    For p95_latency_ms, lower is better, target is the latency budget (ms):
    goodness = clamp(target / max(raw, ε), 0, 1). When raw == 0 (no data),
    we treat it as fully good (1.0).
    """
    if kind in ("judge_pass_rate", "intervention_free_rate"):
        return max(0.0, min(1.0, raw))
    if kind == "silent_fail_rate":
        return max(0.0, min(1.0, 1.0 - raw))
    if kind == "p95_latency_ms":
        if raw <= 0:
            return 1.0
        if target <= 0:
            return 0.0
        return max(0.0, min(1.0, target / raw))
    raise ValueError(f"unknown sli kind: {kind}")


def _target_to_goodness(kind: SliKind, target: float) -> float:
    """Some SLI kinds use a target that's *not* a [0,1] goodness target.

    For p95_latency_ms the target is a latency budget (ms), but the burn math
    needs a [0,1) goodness target. We treat the budget as the "100% good" line,
    i.e. the goodness target degenerates to 0.99 for the purposes of burn math.
    Operators set a hard latency line elsewhere; burn just reflects "how far
    past the line are we".
    """
    if kind == "p95_latency_ms":
        return 0.99
    if kind == "silent_fail_rate":
        # Target is max allowed silent-fail rate; goodness target = 1 - that.
        return max(0.0, min(0.999, 1.0 - target))
    return max(0.0, min(0.999, target))


# -- multi-window helpers --------------------------------------------------


def multi_window_burn_rates(
    query: EventQuery,
    agent_id: str | None,
    task_class: str,
    model_version: str,
    sli_kind: SliKind,
    target: float,
    clock: Clock,
) -> dict[str, BurnRateWindow]:
    """Compute SLI + burn rate over the canonical 1h/6h/24h/7d windows."""
    sli_fn = _sli_fn(sli_kind)
    goodness_target = _target_to_goodness(sli_kind, target)
    out: dict[str, BurnRateWindow] = {}
    for label, secs in BURN_WINDOWS:
        raw = sli_fn(query, agent_id, task_class, model_version, secs, clock)
        goodness = _sli_to_goodness(sli_kind, raw, target)
        br = burn_rate(goodness, goodness_target)
        out[label] = BurnRateWindow(
            label=label,
            window_seconds=secs,
            sli_value=goodness,
            target=goodness_target,
            burn_rate=br,
        )
    return out


def burn_alert_level(burn_rates: dict[str, BurnRateWindow]) -> str:
    """Classify into 'ok' | 'warn' | 'critical' | 'exhausted'.

    Order matters: 'exhausted' (budget already gone) takes priority over
    'critical' (very fast burn but still some budget left).
    """
    one_h = burn_rates.get("1h")
    six_h = burn_rates.get("6h")
    seven_d = burn_rates.get("7d")

    if seven_d is not None and seven_d.burn_rate > 1.0:
        return "exhausted"
    if one_h is not None and one_h.burn_rate > 14.4:
        return "critical"
    if (one_h is not None and one_h.burn_rate > 6.0) or (
        six_h is not None and six_h.burn_rate > 3.0
    ):
        return "warn"
    return "ok"
