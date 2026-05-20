# Wave 4C — Earned Autonomy Gradient

Status: complete. `pytest tests/unit/test_autonomy.py -xvs` → **23 passed**.

## Files added (~540 LOC src, ~370 LOC tests)

- `src/acp/autonomy/__init__.py` — public surface
- `src/acp/autonomy/states.py` — `AutonomyTier`, `TIER_ORDER`, `TIER_DESCRIPTIONS`,
  `tier_index`, `from_index` (clamped)
- `src/acp/autonomy/events.py` — `emit_autonomy_change` writes wide_event
  (event_type='autonomy_change', attrs include old/new tier, cause, burn_rate,
  boundary_delta, optional operator). Each change uses a ULID-suffixed run_id
  so frozen-clock tests don't collide on the per-run chain.
- `src/acp/autonomy/controller.py` — `AutonomyController` with:
  - `current_tier` (never raises; falls back to spec.default_tier, then T1)
  - `initialize_for_agent` (seeds autonomy_state per task_class)
  - `apply_demotion` (no operator required)
  - `apply_promotion` (raises ValueError without operator — asymmetric default)
- `src/acp/autonomy/transitions.py`:
  - `evaluate_burn_state` (stable/warn/critical/exhausted from BudgetSnapshot)
  - `evaluate_promotion_eligibility` (100 consec pass + ≥0.97 rate + ≥72h span,
    T4-cap; reads judgments DESC)
  - `auto_demote_on_burn` (critical → -1, exhausted → -2, clamped at T0; harm
    judgment in last hour → snap to T1)
  - `evaluate_outcome_signals` (>2 rollback_required/oncall_refire in 24h → -1)
  - `run_autonomy_tick` (latest-snapshot SQL + demotions; never auto-promotes)

## Axiom alignment

- Axiom 8 (live daemon, asymmetric defaults): demotion automatic + immediate;
  promotion requires operator signature and is invoked manually from the CLI.
- Axiom 9 (server-side tier, never self-attested): tier state lives in DB; gateway
  reads via `current_tier`. No path exposes tier to the agent.
- Every transition emits an `autonomy_change` wide_event for audit replay.

## Test summary

23 tests cover: tier ordering, controller read fallback, demotion/promotion
mutation + event emission, operator requirement on promotion, burn classification
(all four levels + worst-of), one/two-tier demotion with clamp at T0, organic vs
adversarial budget independence, harm-judgment snap-to-T1, promotion eligibility
positive + four negative paths (recent fail, too few judgments, short window,
T4 cap), outcome-cluster demotion + below-threshold no-op, and three
`run_autonomy_tick` smoke tests (demotes on latest snapshot, ignores stale
critical when newer snapshot is stable, never promotes even with 200 clean
judgments).

## Not in scope (for later waves)

- CLI `acp promote ...` wiring (W5B/W6).
- Gateway tier_check integration (W5/W4A consumer).
- Scheduler lifespan calling `run_autonomy_tick` every 60s (W5A).
