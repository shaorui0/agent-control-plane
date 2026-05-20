# ACP Security Invariants

> Code-level invariants the implementation must uphold. Each is referenced by
> file and line number against the v1.0 source tree.

These are checked, not aspired to: deviations are bugs.

---

## SI-1. Every Gateway request carries a server-issued per-run bearer token

The token is issued by `POST /v1/sessions` and bound to a single `run_id`.
The Gateway rejects any tool-invoke that omits or mismatches the token.

- **Defined**: `src/acp/gateway/auth.py`
- **Enforced**: `src/acp/gateway/routes.py` (FastAPI dependency on every
  `/v1/sessions/{run_id}/*` route)
- **Test**: `tests/unit/test_gateway_invoke.py`

## SI-2. Tool args validate against the tool's Pydantic strict schema

Extra fields → 400. Coerced types → rejected. The schema is the only contract
the agent has with the tool.

- **Defined**: `src/acp/gateway/tools/base.py` (`@sealed_tool` decorator
  attaches a Pydantic `args_model`)
- **Enforced**: `src/acp/gateway/routes.py` invoke handler
- **Test**: `tests/unit/test_gateway_invoke.py` (denial paths for extra/coerced)

## SI-3. Tool name must be in Registry AND in agent's current tier allowlist

`seal_check` first (binding exists for `(agent, tool)`), then `tier_check`
(binding.max_tier respects current tier). O(1) lookup, no wildcards, no regex.

- **seal_check**: `src/acp/gateway/policy.py:40`
- **tier_check**: `src/acp/gateway/policy.py:54`
- **Tests**: `tests/unit/test_gateway_policy.py` (100% line coverage gate)

## SI-4. Idempotency keys are SERVER-issued; agent-supplied keys → 400

The `IdempotencyVault` issues a ULID per `run_id`. Schema-level validation in
`ToolCallRequest` rejects any agent-supplied 26-char string that wasn't
issued by the vault. Defense vs T12.

- **Vault**: `src/acp/gateway/idempotency.py:19` (`IdempotencyVault`)
- **Issue**: `src/acp/gateway/idempotency.py:27` (`issue`)
- **Check + consume**: `src/acp/gateway/idempotency.py:33`
  (`check_and_consume`)
- **Schema-side check**: `src/acp/schemas/tool.py` validates ULID format
- **Test**: `tests/unit/test_idempotency.py`

## SI-5. Every successful Gateway call emits exactly one WideEvent, chained

The event is emitted by `attestation.emit_attested_event`; the chain hash
links to `prev_event_id` for the same `run_id`. The agent has no key and
cannot forge.

- **Emit**: `src/acp/gateway/attestation.py`
- **Chain**: `src/acp/events/store.py:79` (`emit`), `_chain_payload` at
  `src/acp/events/store.py:42`
- **Test**: `tests/unit/test_attestation.py`,
  `tests/unit/test_events_store.py::test_emit_links_chain_and_increments_step`

## SI-6. On any internal exception, Gateway returns generic 503 — no traceback

`DenyClosed` carries a stable short `reason_code` safe to surface; the
`agent_dict()` method hides internal message. Defense vs T15 side-channel.

- **DenyClosed**: `src/acp/errors.py` (with `reason_code`, `agent_dict`)
- **Route handler**: `src/acp/gateway/routes.py` (uses
  `acp_error_envelope(exc)`)
- **Test**: `tests/unit/test_errors.py::test_deny_closed_agent_dict_hides_internal_message`

## SI-7. Only Gateway and Judge processes hold a writable handle to the Event Store

Sandbox has no credential. In v1.0 this is enforced by single-process
architecture: the agent has no socket and no sqlite3 connection.

- **Sole writer**: `src/acp/events/store.py:66` (`WideEventStore`)
- **No agent surface**: `src/acp/gateway/routes.py` exposes no `/v1/events/*`
  write endpoint
- **Test**: `tests/adversarial/test_A04_event_store_isolation.py`

## SI-8. Each event carries h = blake2b(prev_hash || canonical(event))

Verifier detects any tampering offline.

- **Hash**: `src/acp/crypto.py` (`chain_hash`, `_canonical`)
- **Store-side**: `src/acp/events/store.py:42` (`_chain_payload`)
- **Verifier**: `src/acp/events/verifier.py:47` (`verify_run`),
  `src/acp/events/verifier.py:89` (`verify_all`)
- **CLI**: `src/acp/events/verifier.py:102` (`python -m acp.events.verifier`)
- **Test**: `tests/unit/test_events_chain.py`,
  `tests/unit/test_crypto.py::test_verify_chain_detects_payload_tamper`

## SI-9. There is NO update or delete API for the event store. Compaction is v2.

Deliberate inconvenience: disk grows linearly. Necessary to keep the chain
provably append-only.

- **Class**: `src/acp/events/store.py:66` exposes only `emit`, `get_by_id`,
  `tail_run`, `count`. No `update`, no `delete`, no `compact`.
- **DDL**: `src/acp/events/migrations/0001_init.sql` — `wide_events` table
  has no DELETE-permitting triggers

## SI-10. Reads return frozen Pydantic models; mutation attempts raise

All schemas in `src/acp/schemas/*` use `ConfigDict(frozen=True,
extra='forbid')`.

- **Base**: `src/acp/schemas/base.py:37` (`BaseEvent` with frozen config)
- **WideEvent**: `src/acp/schemas/wide_event.py:100`
- **Test**: `tests/unit/test_schemas.py` (32 tests)

## SI-11. Promotion requires (a) ≥ N consecutive task-class successes AND (b) operator signature

The asymmetric rule. `apply_promotion` raises `ValueError` without operator;
eligibility independently checks ≥100 consecutive pass, ≥0.97 rate, ≥72h span.

- **Eligibility**: `src/acp/autonomy/transitions.py`
  (`evaluate_promotion_eligibility`)
- **Apply**: `src/acp/autonomy/controller.py:109` (`apply_promotion`)
- **Test**: `tests/unit/test_autonomy.py` (promotion positive +
  four negative paths)

## SI-12. Demotion is automatic and immediate on SLO burn or any harm verdict

No operator signature required to demote. Critical burn → -1 tier; exhausted
→ -2 tier (clamped at T0). Harm judgment in last hour → snap to T1.

- **Auto-demote**: `src/acp/autonomy/transitions.py` (`auto_demote_on_burn`,
  `evaluate_outcome_signals`)
- **Apply**: `src/acp/autonomy/controller.py:87` (`apply_demotion`)
- **Test**: `tests/unit/test_autonomy.py` (harm-snap-to-T1, one/two-tier
  demotion with T0 clamp)

## SI-13. Tier is never exposed to the agent as a value

The agent learns its tier only by which tool calls succeed. There is no
`GET /v1/sessions/{run_id}/tier` endpoint.

- **No endpoint**: `src/acp/gateway/routes.py` exposes no `tier` field
- **Internal use only**: `src/acp/autonomy/controller.py:46`
  (`current_tier`) used by Gateway middleware
- **Error responses**: `src/acp/errors.py` `DenyClosed.agent_dict()` returns
  generic codes (e.g. `tier_too_high`), never the actual tier value
- **Test**: `tests/adversarial/test_A03_tier_leak.py`
