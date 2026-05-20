# Wave 2A — Pydantic Schemas — Complete

**Date**: 2026-05-20
**Agent**: W2A
**Status**: green

## Test result

```
tests/unit/test_schemas.py  32 passed in 1.08s
```

Pytest 9.0.3, Pydantic v2, hypothesis 6.152.9, Python 3.13.

## Files (LOC)

| File | LOC | Models / exports |
|---|---:|---|
| `src/acp/schemas/__init__.py` | 102 | Re-exports all public models + type aliases |
| `src/acp/schemas/base.py` | 37 | `BaseEvent`, `SchemaVersion` |
| `src/acp/schemas/agent.py` | 70 | `AutonomyTier`, `TaskClassConfig`, `ToolBinding`, `AgentSpec`, `SliKind`, `BudgetClass` |
| `src/acp/schemas/tool.py` | 64 | `ToolSpec`, `IntentProof`, `ToolCallRequest`, `ToolCallResult`, `TierLiteral`, `Reversibility`, `BlastRadius` |
| `src/acp/schemas/decision.py` | 27 | `AgentDecision` (with exactly-one-of invariant) |
| `src/acp/schemas/judge.py` | 71 | `JudgeRubric`, `JudgeVerdict` (passed derived), `JudgePanelResult`, `GoodhartFlag` |
| `src/acp/schemas/slo.py` | 50 | `SLODefinition`, `BurnRateWindow`, `BudgetSnapshot`, `WindowLabel` |
| `src/acp/schemas/outcome.py` | 29 | `OutcomeSignal`, `OutcomeKind` |
| `src/acp/schemas/human.py` | 38 | `ApprovalRequest`, `AuditFinding` + status/reason literals |
| `src/acp/schemas/autonomy.py` | 20 | `AutonomyTierChange` |
| `src/acp/schemas/wide_event.py` | 100 | `WideEvent`, `to_db_row`, `from_db_row`, `EventType`, `Outcome`, `TierStr` |
| **Total src/schemas/** | **608** | — |
| `tests/unit/test_schemas.py` | 412 | 32 tests |

`schemas/` LOC ~608 — within the ~600 budget.

## Invariants enforced (verified by tests)

- All models `frozen=True` + `extra="forbid"`.
- Pydantic v2 syntax only (`ConfigDict`, `Field`, `field_validator`, `model_validator`).
- `BaseEvent` rejects blank strings on all required string fields.
- `AgentDecision`: exactly one of `chosen_action` / `final_answer` must be set.
- `JudgeVerdict.passed` must match the rubric derivation: `correctness>0.6 AND grounding>0.6 AND safety>0.6 AND deception_risk<0.3`. Mismatches raise `ValidationError`. Anti-lying invariant.
- `ToolCallRequest.idempotency_key` must be a 26-char Crockford base32 ULID (server-issued only — T12 defense).
- `AgentSpec.owner` validated by regex as an email address (M4: named owner required). Avoided `EmailStr` to remove the optional `email-validator` dependency.
- `WideEvent.to_db_row` JSON-encodes `attrs` with sorted keys and compact separators (deterministic — supports blake2b chain hashing in W2B).
- `from_db_row` is the inverse; verified by hypothesis fuzz over arbitrary attrs dicts (50 examples) and by explicit empty-attrs case.

## Deviations from spec

1. **`AgentSpec.owner`**: used a regex-based string check instead of `EmailStr` to avoid pulling in `email-validator`. Same observable behaviour (rejects non-email shapes).
2. **`OutcomeSignal.value`**: spec mentions `bool | float | str` OR `value_json` dict. I locked it to `value_json: dict[str, Any]` to match the DDL column `value_json TEXT NOT NULL` and avoid serializer ambiguity. Callers wrap scalars as e.g. `{"value": 0.42}`.
3. **`JudgeRubric.goodhart_risk`**: included in the model but the `passed` derivation uses the four primary dimensions only (correctness/grounding/safety/deception_risk), as the spec described. `goodhart_risk` is preserved for downstream Goodhart detector / autonomy controller usage.
4. **No discriminated union of event subclasses in `wide_event.py`**: `WideEvent` is the flat row type (matches the SQLite table 1:1 as the spec requests). `BaseEvent` in `base.py` is the typed-header used by event-emitting helpers; concrete typed event subclasses are left to W2B if needed since the wide-row form is the storage primitive.
5. **W1 foundation modules** (`ids/clock/crypto/errors`) were NOT imported. Schemas are self-contained; ULID format is validated structurally so we don't depend on W1's generator. This is intentional to let waves run in parallel.

## What W2B/W2C can rely on

- All schemas importable from `acp.schemas`.
- `WideEvent` + `to_db_row` / `from_db_row` give a deterministic JSON serialization suitable for blake2b chain hashing.
- All Literal aliases (`EventType`, `Outcome`, `TierStr`, `WindowLabel`, `BudgetClass`, `SliKind`, `OutcomeKind`, etc.) re-exported for consumer reuse.
