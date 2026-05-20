# Wave 3 — Tool Gateway + Sandbox — Complete

Date: 2026-05-20
Agent: W3

## Files

### Source (src/acp/gateway/, src/acp/sandbox/)

- src/acp/gateway/__init__.py
- src/acp/gateway/auth.py
- src/acp/gateway/policy.py
- src/acp/gateway/intent_check.py
- src/acp/gateway/action_token.py
- src/acp/gateway/idempotency.py
- src/acp/gateway/egress_dlp.py
- src/acp/gateway/budget.py
- src/acp/gateway/attestation.py
- src/acp/gateway/routes.py
- src/acp/gateway/tools/__init__.py
- src/acp/gateway/tools/base.py
- src/acp/gateway/tools/vm_query.py
- src/acp/gateway/tools/loki_query.py
- src/acp/gateway/tools/kubectl_get.py
- src/acp/gateway/tools/kubectl_describe.py
- src/acp/gateway/tools/kubectl_scale.py
- src/acp/gateway/tools/kubectl_rollout.py
- src/acp/gateway/tools/slack_post.py
- src/acp/gateway/tools/runbook_search.py
- src/acp/sandbox/__init__.py
- src/acp/sandbox/trajectory.py
- src/acp/sandbox/fanout.py
- src/acp/sandbox/budgets.py

Total Wave 3 source: ~1730 LOC (target was ~1500).

### Tests (tests/unit/)

- tests/unit/test_gateway_policy.py        (28 tests, 100% coverage on policy.py)
- tests/unit/test_gateway_invoke.py        (14 tests, full POST /v1/.../invoke happy + 6 denial paths)
- tests/unit/test_intent_check.py          (5 tests)
- tests/unit/test_idempotency.py           (6 tests)
- tests/unit/test_action_token.py          (7 tests)
- tests/unit/test_egress_dlp.py            (10 tests)
- tests/unit/test_budget.py                (7 tests)
- tests/unit/test_attestation.py           (3 tests)
- tests/unit/test_sandbox.py               (6 tests)

Total Wave 3 tests: ~1100 LOC, 86 test cases.

## Coverage gate

policy.py — 100% line coverage:

```
Name                        Stmts   Miss  Cover   Missing
---------------------------------------------------------
src/acp/gateway/policy.py      80      0   100%
---------------------------------------------------------
TOTAL                          80      0   100%
```

Command:
```
.venv/bin/python -m pytest tests/unit/test_gateway_policy.py --cov=acp.gateway.policy --cov-report=term-missing
```

## Test output (last 30 lines)

```
tests/unit/test_action_token.py::test_double_consume PASSED              [ 67%]
tests/unit/test_action_token.py::test_malformed_token PASSED             [ 68%]
tests/unit/test_action_token.py::test_invalid_signature PASSED           [ 69%]
tests/unit/test_egress_dlp.py::test_clean_args_pass PASSED               [ 70%]
tests/unit/test_egress_dlp.py::test_aws_key_flagged PASSED               [ 72%]
tests/unit/test_egress_dlp.py::test_github_pat_flagged PASSED            [ 73%]
tests/unit/test_egress_dlp.py::test_bearer_flagged PASSED                [ 74%]
tests/unit/test_egress_dlp.py::test_jwt_flagged PASSED                   [ 75%]
tests/unit/test_egress_dlp.py::test_base64_blob_flagged PASSED           [ 76%]
tests/unit/test_egress_dlp.py::test_high_entropy_flagged PASSED          [ 77%]
tests/unit/test_egress_dlp.py::test_nested_walk PASSED                   [ 79%]
tests/unit/test_egress_dlp.py::test_list_walk PASSED                     [ 80%]
tests/unit/test_egress_dlp.py::test_assert_raises_denyclosed PASSED      [ 81%]
tests/unit/test_budget.py::test_check_and_reserve_within_cap PASSED      [ 82%]
tests/unit/test_budget.py::test_check_and_reserve_tokens_exceeded PASSED [ 83%]
tests/unit/test_budget.py::test_check_and_reserve_usd_exceeded PASSED    [ 84%]
tests/unit/test_budget.py::test_record_actual_upserts PASSED             [ 86%]
tests/unit/test_budget.py::test_unknown_agent_denies PASSED              [ 87%]
tests/unit/test_budget.py::test_negative_estimate_rejected PASSED        [ 88%]
tests/unit/test_budget.py::test_post_reserve_then_check_uses_accumulated PASSED [ 89%]
tests/unit/test_attestation.py::test_emit_attaches_args_hash PASSED      [ 90%]
tests/unit/test_attestation.py::test_emit_attaches_result_hash PASSED    [ 91%]
tests/unit/test_attestation.py::test_emit_extra_attrs_merged PASSED      [ 93%]
tests/unit/test_sandbox.py::test_trajectory_step_cap PASSED              [ 94%]
tests/unit/test_sandbox.py::test_trajectory_default_cap_is_20 PASSED     [ 95%]
tests/unit/test_sandbox.py::test_step_budget PASSED                      [ 96%]
tests/unit/test_sandbox.py::test_parallel_subagents_joins_all PASSED     [ 97%]
tests/unit/test_sandbox.py::test_parallel_subagents_default_echo PASSED  [ 98%]
tests/unit/test_sandbox.py::test_parallel_subagents_captures_child_errors PASSED [100%]

============================== 86 passed in 0.57s ==============================
```

Full suite (`pytest -q`) at end of Wave 3: **262 passed**, no regressions.

## Deviations from plan

1. **`AutonomyProvider` interface**: defined as a `Protocol` in `gateway/routes.py` with
   a `DefaultAutonomyProvider` that returns the spec's `default_tier`. Marked
   TODO(W4C) for integration with the live controller from autonomy/controller.py.

2. **`T3 promotion in tests`**: the registry validator (W2C) forbids `default_tier: T3`
   in YAML (T3/T4 must be earned via promotion). For T3-path tests
   (intent_missing, kwargs_constraint_violation, approval queue), tests inject
   a `_FixedTier(T3)` provider on the `GatewayDeps` instance — simulating
   what W4C will do live.

3. **`migrate()` vs `RegistryStore` schema mismatch**: pre-existing across waves —
   `events/migrations/0001_init.sql` creates a narrow `agents` table; RegistryStore
   wants a richer one. In tests we `DROP TABLE agents` after migrate so RegistryStore's
   own DDL can apply. Noting here so a future W5A migration consolidation can fix it.

4. **`exception_handler` removed from APIRouter**: `APIRouter` has no `exception_handler`
   decorator. Exposed `acp_error_envelope(exc)` helper instead — W5A will wire this
   at the FastAPI app level.

5. **`action_token.py` args_hash mismatch**: signature binds args_hash, so a
   mismatched-args call usually trips signature verification first. Test
   `test_db_args_hash_mismatch_detected` explicitly exercises the
   `action_token_bound_mismatch` branch by tampering with the DB row.

## Threat coverage notes

- T12 (idempotency forgery) → server-issued ULID-only vault. Agent-supplied keys denied.
- T13 (action token replay/forge) → HMAC-bound (run_id, tool, args_hash, exp); single-consume.
- T15 (egress DLP) → 6 regex/entropy detectors + recursive arg walk.
- K4 (deceptive alignment) → seal_check + tier_check + intent_check chain in `policy.py`;
  T3+ tools forced into the approvals table (never auto-execute).
- Axiom 4 (outcome derived) → `attestation.emit_attested_event` sets `outcome` only
  from Gateway observation; `agent_claim` is a separate untrusted column.

## Hand-off to W4C

- Replace `DefaultAutonomyProvider` with `autonomy.controller.AutonomyController`.
- Wire the live `current_tier(agent_id, task_class)` query into `GatewayDeps.autonomy`.
- The approval-decision endpoint and post-decision tool execution are NOT in
  Wave 3 (live in `human/` — Wave 5). The Gateway only writes the `pending` row.
