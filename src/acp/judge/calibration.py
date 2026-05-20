"""Sampling + drift detection for the judge calibration loop.

Stratified random sampling pushes a fraction of judgments into the human
audit queue. The realized human label is recorded in `calibration`; from
that table we compute per-(judge_model, task_class) precision/recall and
detect drift week-over-week.

Per MASTER_PLAN.md section 8 K3 Goodhart + K2 silent failure.
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct
from typing import Mapping

from acp.ids import new_ulid


DEFAULT_TIER_SAMPLE_RATES: Mapping[str, float] = {
    "T0": 0.005,
    "T1": 0.005,
    "T2": 0.03,
    "T3": 0.10,
    "T4": 1.0,
}


def _seeded_uniform(seed: str) -> float:
    """Deterministic uniform draw in [0, 1) from a string seed.

    Uses blake2b → first 8 bytes as a uint64, divided by 2^64. We use this
    instead of `random.random()` so tests are reproducible without monkey-
    patching global RNG state.
    """
    h = hashlib.blake2b(seed.encode("utf-8"), digest_size=8).digest()
    (val,) = struct.unpack("<Q", h)
    return val / float(1 << 64)


def maybe_sample_for_calibration(
    event_id: str,
    current_tier: str,
    conn: sqlite3.Connection,
    *,
    sample_rates: Mapping[str, float] = DEFAULT_TIER_SAMPLE_RATES,
    seed_salt: str = "calibration-v1",
) -> bool:
    """Decide whether to enqueue this event for human review.

    Sampling is deterministic per event_id (so tests are reproducible) and
    stratified by tier. T4 always samples (rate=1.0). On hit, an entry is
    inserted into `audit_queue` with `reason="sample"`.
    """
    rate = float(sample_rates.get(current_tier, 0.0))
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        sampled = True
    else:
        draw = _seeded_uniform(f"{seed_salt}:{event_id}")
        sampled = draw < rate
    if not sampled:
        return False
    conn.execute(
        "INSERT INTO audit_queue (audit_id, event_id, reason, status) "
        "VALUES (?, ?, 'sample', 'pending')",
        (new_ulid(), event_id),
    )
    conn.commit()
    return True


def record_calibration(
    event_id: str,
    judge_panel_label: str,
    human_label: str,
    judge_model: str,
    task_class: str,
    conn: sqlite3.Connection,
    ts_ms: int,
) -> str:
    """Insert a calibration row; `delta=1` if labels disagree else 0."""
    delta = 0 if judge_panel_label == human_label else 1
    cid = new_ulid()
    conn.execute(
        "INSERT INTO calibration "
        "(cal_id, event_id, judge_panel_label, human_label, delta, judge_model, task_class, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, event_id, judge_panel_label, human_label, delta, judge_model, task_class, ts_ms),
    )
    conn.commit()
    return cid


def _precision_recall(
    conn: sqlite3.Connection,
    judge_model: str,
    task_class: str,
    since_ms: int,
    until_ms: int,
) -> tuple[float, float, int]:
    """Compute (precision, recall, n) over [since_ms, until_ms).

    Treat human_label=='pass' as the gold positive. Precision = TP / (TP + FP),
    recall = TP / (TP + FN). Empty → (0.0, 0.0, 0).
    """
    rows = conn.execute(
        "SELECT judge_panel_label, human_label FROM calibration "
        "WHERE judge_model = ? AND task_class = ? AND ts >= ? AND ts < ?",
        (judge_model, task_class, since_ms, until_ms),
    ).fetchall()
    if not rows:
        return 0.0, 0.0, 0
    tp = fp = fn = 0
    for jp, hu in rows:
        jp_pass = jp == "pass"
        hu_pass = hu == "pass"
        if jp_pass and hu_pass:
            tp += 1
        elif jp_pass and not hu_pass:
            fp += 1
        elif not jp_pass and hu_pass:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return precision, recall, len(rows)


def drift_alarm(
    conn: sqlite3.Connection,
    *,
    now_ms: int,
    window_days: int = 7,
    drift_threshold_pp: float = 0.05,
) -> dict[str, dict[str, float | bool]]:
    """Compare current vs previous `window_days` per (judge_model, task_class).

    Returns a dict keyed `f"{judge_model}|{task_class}"` with current/previous
    precision/recall and a boolean `judge_drift` if either dropped > threshold.
    """
    win = window_days * 24 * 3600 * 1000
    cur_lo, cur_hi = now_ms - win, now_ms
    prev_lo, prev_hi = now_ms - 2 * win, now_ms - win

    keys = conn.execute(
        "SELECT DISTINCT judge_model, task_class FROM calibration "
        "WHERE ts >= ? AND ts < ?",
        (prev_lo, cur_hi),
    ).fetchall()

    out: dict[str, dict[str, float | bool]] = {}
    for jm, tc in keys:
        cp, cr, cn = _precision_recall(conn, jm, tc, cur_lo, cur_hi)
        pp, pr, pn = _precision_recall(conn, jm, tc, prev_lo, prev_hi)
        # Drift if previous had data AND drop > threshold.
        drift_p = pn > 0 and (pp - cp) > drift_threshold_pp
        drift_r = pn > 0 and (pr - cr) > drift_threshold_pp
        out[f"{jm}|{tc}"] = {
            "precision_current": cp,
            "precision_previous": pp,
            "recall_current": cr,
            "recall_previous": pr,
            "n_current": cn,
            "n_previous": pn,
            "judge_drift": bool(drift_p or drift_r),
        }
    return out
