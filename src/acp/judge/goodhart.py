"""Goodhart heuristics — fast, no LLM.

Four detectors operate on AgentDecision + surrounding wide events. They return
GoodhartFlag(s) (or None) and are aggregated by `detect_all`. Flags are
persisted to the `goodhart_flags` table for K3 defense.

Per MASTER_PLAN.md axiom 4 (outcome is derived) and section 8 K3 Goodhart.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Iterable, Sequence

from acp.ids import new_ulid
from acp.schemas.decision import AgentDecision
from acp.schemas.judge import GoodhartFlag
from acp.schemas.wide_event import WideEvent


# ---- individual detectors ---------------------------------------------------


def length_anomaly(
    decision: AgentDecision,
    baseline_p95_chars: float,
) -> GoodhartFlag | None:
    """Reasoning > 3x baseline p95 length → likely verbose-CoT inflation (K3)."""
    actual = len(decision.reasoning or "")
    if baseline_p95_chars <= 0:
        return None
    if actual <= baseline_p95_chars * 3:
        return None
    severity = "high" if actual > baseline_p95_chars * 5 else "med"
    return GoodhartFlag(
        signal="length_anomaly",
        evidence={
            "actual_chars": actual,
            "baseline_p95_chars": baseline_p95_chars,
            "ratio": actual / baseline_p95_chars,
        },
        severity=severity,  # type: ignore[arg-type]
    )


_TOOL_TOKEN = re.compile(r"\b([a-z][a-z0-9_]*[a-z0-9])\b")


def reasoning_action_mismatch(decision: AgentDecision) -> GoodhartFlag | None:
    """If reasoning name-drops a tool but the chosen action calls a different one.

    The heuristic looks for any tool-name-shaped token in reasoning that ends
    with `(` or appears as ``tool_name`` or `tool_name:`. If at least one such
    token is found and the chosen tool is not among them, flag it.
    """
    if decision.chosen_action is None:
        return None
    reasoning = decision.reasoning or ""
    if not reasoning:
        return None
    # Pull out tokens that look like tool refs: `call X`, `X(`, `tool_name`.
    candidates: set[str] = set()
    for m in re.finditer(r"`([a-z][a-z0-9_]+)`", reasoning):
        candidates.add(m.group(1))
    for m in re.finditer(r"\b([a-z][a-z0-9_]+)\s*\(", reasoning):
        candidates.add(m.group(1))
    for m in re.finditer(
        r"(?:call|invoke|use|run)\s+([a-z][a-z0-9_]+)", reasoning, flags=re.IGNORECASE
    ):
        candidates.add(m.group(1).lower())
    if not candidates:
        return None
    chosen = decision.chosen_action.tool_name
    if chosen in candidates:
        return None
    return GoodhartFlag(
        signal="reasoning_action_mismatch",
        evidence={
            "reasoning_mentions": sorted(candidates),
            "chosen_tool": chosen,
        },
        severity="med",
    )


def self_citation(
    decision: AgentDecision,
    prior_events: Sequence[WideEvent],
) -> GoodhartFlag | None:
    """Agent quotes its own prior reasoning/decision as evidence with no
    tool call between the prior agent_decision and the current one.
    """
    reasoning = decision.reasoning or ""
    if not reasoning or not prior_events:
        return None

    # Identify the last prior reasoning-bearing event (task_start carries the
    # initial reasoning; task_end carries the final reasoning) and the last
    # tool_call/result. If a previous reasoning event exists and no tool call
    # has been recorded since, then this fresh reasoning re-quoting it has
    # no new external evidence to ground itself in.
    last_decision_step = -1
    last_tool_step = -1
    last_decision_attrs: dict | None = None
    for e in prior_events:
        attrs = e.attrs or {}
        if attrs.get("reasoning"):
            if e.step > last_decision_step:
                last_decision_step = e.step
                last_decision_attrs = attrs
        if e.event_type in ("tool_call", "tool_result"):
            last_tool_step = max(last_tool_step, e.step)

    if last_decision_step < 0 or last_decision_attrs is None:
        return None
    # No tool call between the last decision and now → potential self-cite.
    if last_tool_step > last_decision_step:
        return None

    prior_text = (last_decision_attrs.get("reasoning") or "")[:200]
    if not prior_text or len(prior_text) < 40:
        return None
    # Substring overlap → self-citation signal.
    snippet = prior_text[:60].strip()
    if snippet and snippet in reasoning:
        return GoodhartFlag(
            signal="self_citation",
            evidence={
                "prior_step": last_decision_step,
                "snippet": snippet[:80],
            },
            severity="med",
        )
    return None


def metric_local(
    decision: AgentDecision,
    slo_history: Sequence[dict] | None = None,
) -> GoodhartFlag | None:
    """Deferred metric-local Goodhart (W4B fills the real check).

    Placeholder hook: returns a low-severity flag iff slo_history shows
    `judge_pass_rate` improving while `intervention_free_rate` is regressing —
    a classic metric-gaming pattern. With no history available, returns None.
    """
    if not slo_history:
        return None
    # Need at least two consecutive snapshots for a delta.
    if len(slo_history) < 2:
        return None
    prev, cur = slo_history[-2], slo_history[-1]
    judge_delta = float(cur.get("judge_pass_rate", 0)) - float(prev.get("judge_pass_rate", 0))
    intv_delta = float(cur.get("intervention_free_rate", 0)) - float(
        prev.get("intervention_free_rate", 0)
    )
    if judge_delta > 0.05 and intv_delta < -0.05:
        return GoodhartFlag(
            signal="metric_local",
            evidence={
                "judge_pass_delta": judge_delta,
                "intervention_free_delta": intv_delta,
            },
            severity="high",
        )
    return None


# ---- aggregate + persist ----------------------------------------------------


def detect_all(
    decision: AgentDecision,
    prior_events: Sequence[WideEvent],
    *,
    baseline_p95_chars: float = 0.0,
    slo_history: Sequence[dict] | None = None,
) -> list[GoodhartFlag]:
    """Run all four detectors; return all that fire."""
    flags: list[GoodhartFlag] = []
    for fn in (
        length_anomaly(decision, baseline_p95_chars),
        reasoning_action_mismatch(decision),
        self_citation(decision, prior_events),
        metric_local(decision, slo_history),
    ):
        if fn is not None:
            flags.append(fn)
    return flags


def persist_flags(
    conn: sqlite3.Connection,
    event_id: str,
    flags: Iterable[GoodhartFlag],
    ts_ms: int,
) -> int:
    """Write each flag to `goodhart_flags`. Returns number persisted."""
    n = 0
    cur = conn.cursor()
    for f in flags:
        cur.execute(
            "INSERT INTO goodhart_flags (flag_id, event_id, signal, evidence_json, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                new_ulid(),
                event_id,
                f.signal,
                json.dumps({"evidence": f.evidence, "severity": f.severity}, sort_keys=True),
                ts_ms,
            ),
        )
        n += 1
    conn.commit()
    return n
