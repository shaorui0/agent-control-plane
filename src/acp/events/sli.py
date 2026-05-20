"""SLI computations — pure queries over wide_events.

Per design axiom 6, the SLI is not a counter; it is a query over the typed
event stream filtered by (agent_id, task_class, model_version, time window).

All four functions accept an `EventQuery` and a `clock` to compute the window.
They return floats in [0.0, 1.0] (or ms for p95). Empty window → 0.0.
"""

from __future__ import annotations

from typing import Any

from acp.clock import Clock
from acp.events.query import EventQuery
from acp.schemas.wide_event import WideEvent


def _filter_by_key(
    query: EventQuery,
    agent_id: str | None,
    task_class: str,
    model_version: str,
    since_ms: int,
    until_ms: int,
    event_type: str,
) -> list[WideEvent]:
    """Pull all events of one type for a per-(agent/class/model) window."""
    if agent_id is not None:
        events = list(
            query.by_agent_class_window(
                agent_id, task_class, model_version, since_ms, until_ms
            )
        )
    else:
        events = list(
            query.by_task_class_window(task_class, model_version, since_ms, until_ms)
        )
    return [e for e in events if e.event_type == event_type]


def _window(clock: Clock, window_seconds: int) -> tuple[int, int]:
    now = clock.now()
    return now - window_seconds * 1000, now + 1  # +1 so events stamped == now are included


def _attr(event: WideEvent, key: str, default: Any = None) -> Any:
    return event.attrs.get(key, default)


# -- core SLIs ------------------------------------------------------------


def judge_pass_rate(
    query: EventQuery,
    agent_id: str | None,
    task_class: str,
    model_version: str,
    window_seconds: int,
    clock: Clock,
) -> float:
    """Fraction of judgment events whose verdict == 'pass' in the window."""
    since, until = _window(clock, window_seconds)
    judgments = _filter_by_key(
        query, agent_id, task_class, model_version, since, until, "judgment"
    )
    if not judgments:
        return 0.0
    passes = sum(1 for j in judgments if _attr(j, "verdict") == "pass")
    return passes / len(judgments)


def intervention_free_rate(
    query: EventQuery,
    agent_id: str | None,
    task_class: str,
    model_version: str,
    window_seconds: int,
    clock: Clock,
) -> float:
    """1 - (interventions / total task_ends) within the window. 0.0 if no ends."""
    since, until = _window(clock, window_seconds)
    ends = _filter_by_key(
        query, agent_id, task_class, model_version, since, until, "task_end"
    )
    if not ends:
        return 0.0
    interventions = _filter_by_key(
        query, agent_id, task_class, model_version, since, until, "intervention"
    )
    # A run is interrupted by an intervention; count distinct intervened runs.
    intervened_runs = {ev.run_id for ev in interventions}
    affected = sum(1 for e in ends if e.run_id in intervened_runs)
    return max(0.0, 1.0 - (affected / len(ends)))


def silent_fail_rate(
    query: EventQuery,
    agent_id: str | None,
    task_class: str,
    model_version: str,
    window_seconds: int,
    clock: Clock,
) -> float:
    """K2 silent-failure SLI.

    Fraction of judgments where the *original* verdict was 'pass' AND the
    judgment has been retroactively flipped (attrs.retroactively_flipped == 1
    or attrs.original_verdict == 'pass' and attrs.verdict != 'pass'), over total
    'pass'-or-flipped judgments.
    """
    since, until = _window(clock, window_seconds)
    judgments = _filter_by_key(
        query, agent_id, task_class, model_version, since, until, "judgment"
    )
    # Original pass set = currently pass + currently-flipped-from-pass.
    original_pass = [
        j
        for j in judgments
        if _attr(j, "verdict") == "pass"
        or (
            _attr(j, "retroactively_flipped") in (1, True)
            and _attr(j, "original_verdict") == "pass"
        )
    ]
    if not original_pass:
        return 0.0
    flipped = sum(
        1
        for j in original_pass
        if _attr(j, "retroactively_flipped") in (1, True)
        and _attr(j, "original_verdict") == "pass"
    )
    return flipped / len(original_pass)


def p95_latency_ms(
    query: EventQuery,
    agent_id: str | None,
    task_class: str,
    model_version: str,
    window_seconds: int,
    clock: Clock,
) -> float:
    """95th-percentile task latency from task_end attrs.duration_ms."""
    since, until = _window(clock, window_seconds)
    ends = _filter_by_key(
        query, agent_id, task_class, model_version, since, until, "task_end"
    )
    durations = sorted(
        float(_attr(e, "duration_ms", 0.0))
        for e in ends
        if _attr(e, "duration_ms") is not None
    )
    if not durations:
        return 0.0
    # Nearest-rank percentile (matches Prometheus histogram_quantile-style upper bound).
    idx = max(0, min(len(durations) - 1, int(round(0.95 * (len(durations) - 1)))))
    return durations[idx]
