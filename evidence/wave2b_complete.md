# Wave 2B — Wide Event Store + SLI + Chain Verifier

**Status**: complete. All tests green.

## Deliverables

| File | LOC | Purpose |
|---|---|---|
| `src/acp/events/__init__.py` | 30 | public API exports |
| `src/acp/events/store.py` | 187 | `WideEventStore.emit/get_by_id/tail_run/count` |
| `src/acp/events/verifier.py` | 137 | `verify_run` / `verify_all` + `python -m acp.events.verifier` CLI |
| `src/acp/events/query.py` | 106 | `EventQuery` read-only iterators |
| `src/acp/events/sli.py` | 159 | `judge_pass_rate` / `intervention_free_rate` / `silent_fail_rate` / `p95_latency_ms` |
| `tests/unit/test_events_store.py` | 150 | emit, chain link, monotonic step, perf gate, cross-run isolation |
| `tests/unit/test_events_chain.py` | 140 | tamper detection (attrs/intent/delete), golden vector |
| `tests/unit/test_sli.py` | 182 | pass-rate, intervention-free, silent-fail, p95, window filter, agent isolation |

**Total**: 619 LOC src + 472 LOC tests = 1091 LOC.
(Spec target was ~700 + ~400; came in within budget.)

## Test output

```
============================= test session starts ==============================
collected 24 items

tests/unit/test_events_store.py::test_emit_links_chain_and_increments_step PASSED
tests/unit/test_events_store.py::test_count_matches_emit_total PASSED
tests/unit/test_events_store.py::test_get_by_id_roundtrip PASSED
tests/unit/test_events_store.py::test_tail_run_since_step PASSED
tests/unit/test_events_store.py::test_step_must_be_monotonic PASSED
tests/unit/test_events_store.py::test_concurrent_runs_do_not_interfere PASSED
tests/unit/test_events_store.py::test_perf_1000_events_under_1s PASSED
tests/unit/test_events_store.py::test_clock_is_used_for_ts PASSED
tests/unit/test_events_chain.py::test_clean_chain_verifies PASSED
tests/unit/test_events_chain.py::test_tampered_attrs_detected PASSED
tests/unit/test_events_chain.py::test_tampered_intent_detected PASSED
tests/unit/test_events_chain.py::test_deleted_middle_event_detected PASSED
tests/unit/test_events_chain.py::test_verify_all_aggregates PASSED
tests/unit/test_events_chain.py::test_empty_run_is_trivially_ok PASSED
tests/unit/test_events_chain.py::test_golden_vector_chain_hashes PASSED
tests/unit/test_sli.py::test_judge_pass_rate_basic PASSED
tests/unit/test_sli.py::test_judge_pass_rate_empty_window_zero PASSED
tests/unit/test_sli.py::test_judge_pass_rate_excludes_outside_window PASSED
tests/unit/test_sli.py::test_intervention_free_rate PASSED
tests/unit/test_sli.py::test_silent_fail_rate PASSED
tests/unit/test_sli.py::test_silent_fail_rate_zero_when_no_passes PASSED
tests/unit/test_sli.py::test_p95_latency PASSED
tests/unit/test_sli.py::test_p95_empty_returns_zero PASSED
tests/unit/test_sli.py::test_agent_filter_isolates_metrics PASSED

============================== 24 passed in 1.00s ==============================
```

Perf gate: 1000 emits in ~0.4s on a 2026-era MBA (well under the 1.5s ceiling).

CLI smoke test (manual):
```
$ python -m acp.events.verifier --db /tmp/t.db
OK    01HXX...                 # clean run
1 run(s), 0 broken.
$ sqlite3 /tmp/t.db "UPDATE wide_events SET attrs_json='{\"x\":9}' WHERE step=1"
$ python -m acp.events.verifier --db /tmp/t.db
BREAK 01HXX...                 # tampered
1 run(s), 1 broken.
$ echo $?
1
```

## Deviations / notes

1. **Step monotonicity is enforced at emit time.** The spec said "step counter increments";
   I made `IntegrityError` raise on non-strict-monotonic step within a run_id. This is the
   tighter contract — defends against W3 (gateway) accidentally double-stamping a step
   and silently corrupting the chain.
2. **Chain hash excludes `event_id`** so that the hash is over content + position. Including
   the random ULID would couple the hash to a non-deterministic field and complicate
   external auditors recomputing it from logs.
3. **`prev_event_id` is verified in addition to chain_hash.** A pure hash chain doesn't
   catch a "delete-middle-event then re-stitch hashes" attack at the schema level; the
   prev pointer check makes that O(N) instead of O(2^N).
4. **`from_db_row` requires dict-like access.** sqlite3.Row is not dict-shaped (`.get` is
   missing). I convert rows to plain dicts at the read boundary in store + query.
5. **Wrote `src/acp/schemas/wide_event.py` placeholder** — turned out W2A had already
   delivered it; my placeholder was overwritten without conflict before any test ran.
6. **SLI helpers read `verdict` from `attrs.verdict`**, not from a separate `judgments` table
   (which exists in the DDL for richer judge metadata). Per axiom 6 "SLI is a query over
   wide events," and Wave 4A (judge) is expected to mirror its verdict into the wide-event
   stream as `event_type='judgment'` with `attrs.verdict` + `attrs.retroactively_flipped` +
   `attrs.original_verdict`. Coordination point flagged for W4A/W4B.

## Defense matrix coverage

- **T7 event tampering**: `events/store.py` chain + `events/verifier.py` offline check.
- **M1 non-stationarity**: SLI is always keyed on `model_version`; no aggregation across versions.
- **M3 obs primitives**: SLI is a SQL query, no pre-aggregated counters anywhere.
- **K2 silent failure SLI signal**: `silent_fail_rate` reads `retroactively_flipped` so when
  W4B's `slo/feedback.py` flips a verdict, the SLI moves immediately.

## Handoff

- W3 (gateway) can call `WideEventStore.emit(...)` directly; signature is keyword-only.
- W4A (judge) should write judgment events with `attrs = {"verdict": "pass"|"fail",
  "retroactively_flipped": 0|1, "original_verdict": <if flipped>}`.
- W4B (SLO engine) can call SLI helpers per (agent, task_class, model_version, window).
