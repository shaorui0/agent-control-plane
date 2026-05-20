# Wave 4B: SLO Engine + Outcome Feedback — Complete

**Date**: 2026-05-20
**Agent**: W4B
**Scope**: `src/acp/slo/` — definitions, burn rate math, budget, retroactive
verdict-flip feedback loop, periodic engine, alert sinks + router.

## Deliverables

### Source (`src/acp/slo/`)
| File | LOC | Purpose |
|---|---|---|
| `__init__.py` | 49 | Public surface |
| `definitions.py` | 98 | `SLODefinitionRegistry` + `parse_window`; derives organic + adversarial SLODefinitions from YAML registry |
| `burnrate.py` | 172 | `burn_rate`, `multi_window_burn_rates`, `burn_alert_level` (ok/warn/critical/exhausted) — Google SRE workbook math |
| `budget.py` | 66 | `budget_remaining`, `split_events_by_budget_class` (organic vs adversarial via `goodhart_flags` join) |
| `feedback.py` | 282 | K2 defense: `ingest_outcome_signal`, `maybe_flip_verdict`, `silent_failure_detector` |
| `engine.py` | 189 | `SLOEngine.evaluate_all` (writes `slo_snapshots`) + `start_scheduler` (APScheduler `BackgroundScheduler`) |
| `alerts.py` | 130 | `AlertSink` ABC, `StdoutSink`, `FileSink`, `WebhookSink`, `AlertRouter` (rate-limited per (agent, task_class, level)) |
| **Total src** | **986** | |

### Tests (`tests/unit/`)
| File | LOC | Tests |
|---|---|---|
| `test_burnrate.py` | 136 | 11 (pure math + classification + multi-window) |
| `test_outcome_feedback.py` | 180 | 6 (oncall_refire, rollback, silent-failure 48h, no-flip when git_applied, idempotent flip, adversarial split) |
| `test_slo_engine.py` | 199 | 12 (parse_window, definitions, engine writes snapshots, alert rate-limit, sink IO) |
| **Total tests** | **515** | **29** |

Total LOC delivered: ~1501 lines (986 src + 515 tests).

## Test results

```
$ python -m pytest tests/unit/test_slo_*.py tests/unit/test_burnrate.py tests/unit/test_outcome_feedback.py -xvs
29 passed in 0.26s
```

Full suite: `175 passed, 1 failed` — the failure (`test_calibration.py::test_drift_alarm_detects_precision_drop`) is in Wave 4A territory (`judge/calibration.py`), not W4B scope.

## Design highlights

### Retroactive verdict flip (axiom 10 / K2 defense)
The flip path performs *two* writes for full auditability:

1. `UPDATE judgments SET verdict='fail', retroactively_flipped=1, original_verdict='pass'`
2. New `wide_event` of `event_type='outcome'` with `attrs.retroactive_fail=True`,
   `attrs.original_verdict`, `attrs.judgment_id`, `attrs.reason`.

Triggers (any one is sufficient):
- `oncall_refire` signal arrives within 24h of the pass judgment
- `rollback_required` signal at any time
- No `git_applied` signal within 48h (silent failure — swept by `silent_failure_detector`)

The flip is idempotent: the second `maybe_flip_verdict` call on an already-flipped run returns `False` without re-writing.

### Adversarial budget carve-out (M2)
`split_events_by_budget_class(events, conn)` partitions a stream of `WideEvent`s
by joining against `goodhart_flags.event_id`. Adversarial events are tracked
in a separate `slo_snapshots` row with `budget_class='adversarial'` so a
prompt-injection failure cannot consume the organic SLO budget.

### Multi-window burn rate
`BURN_WINDOWS` = `[(1h,3600), (6h,21600), (24h,86400), (7d,604800)]`. Each
window's `BurnRateWindow` is keyed off the corresponding SLI query (no
pre-aggregation — axiom 6). Alert classification:
- `exhausted` (7d burn > 1.0) — priority
- `critical` (1h > 14.4×)
- `warn` (1h > 6× or 6h > 3×)
- `ok` otherwise

### Alert rate-limiting
`AlertRouter` keys `(agent_id, task_class, level)` → last-emit-ms; the same
(agent, task_class, level) tuple fires at most once per hour. Distinct levels
(e.g. warn + critical concurrently) are not suppressed against each other. An
`ok` transition clears the dedup map for the (agent, task_class) so the next
burn alerts immediately.

### SLI goodness coercion
Some SLIs (`silent_fail_rate` — lower is better; `p95_latency_ms` — lower is
better, target in ms) need to be coerced into a [0,1] "goodness" measure
before the burn-rate formula applies. The dispatcher in `burnrate.py` and
`engine.py` does this consistently.

## Notes / follow-ups (not blocking ship)
- `WebhookSink` uses `httpx` and swallows network errors — alerting must never
  crash the engine. A future wave can wire this to a real Slack/PagerDuty.
- `_apply_flip` lazily `ALTER TABLE judgments ADD COLUMN original_verdict` for
  backwards compatibility with older DBs. Future migration should add the column
  upfront.
- `RegistryStore` requires its own SQLite connection (its DDL collides with the
  minimal `agents` table in `0001_init.sql`). Tests use `sqlite3.connect(":memory:")`
  to sidestep — production wiring lives in Wave 5A's `server.py` lifespan.
