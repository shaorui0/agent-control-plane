# Wave 2C — Agent Registry + YAML specs — Complete

**Date**: 2026-05-20
**Agent**: W2C
**Scope**: L0 registry (loader, validator, store) + two production YAML agent specs + unit tests.

## Deliverables

### Source (src/acp/registry/)
| File | LOC | Purpose |
|---|---:|---|
| `__init__.py` | 30 | Public API re-export |
| `models.py` | 33 | Re-exports from `schemas/agent.py` + `LoadedRegistry` typed dict |
| `loader.py` | 79 | `load_dir()` + `install_sighup_handler()` |
| `validator.py` | 108 | Registry rules (M4 owner, K4 sealed tools, mutating-tool tier floor, budgets, default_tier) |
| `store.py` | 121 | `RegistryStore`: in-memory cache + SQLite UPSERT mirror + tool binding O(1) lookup |
| **Total src** | **371** | |

### Agent YAML (agents/)
- `oncall_triage.yaml` (46 lines): 2 task_classes, 8 sealed tools, mutating ones at T3 with intent.
- `code_reviewer.yaml` (24 lines): 1 task_class, 3 read/post tools — second agent enables cross-task-class SLO sanity.

### Tests (tests/unit/test_registry.py)
- 308 LOC, 15 tests, all passing.

## Test output

```
============================= test session starts ==============================
platform darwin -- Python 3.13.3, pytest-9.0.3
collected 15 items

tests/unit/test_registry.py::test_load_dir_real_agents PASSED
tests/unit/test_registry.py::test_validator_rejects_missing_owner PASSED
tests/unit/test_registry.py::test_validator_rejects_mutating_tool_below_t3 PASSED
tests/unit/test_registry.py::test_validator_rejects_default_tier_t4 PASSED
tests/unit/test_registry.py::test_validator_rejects_default_tier_t3 PASSED
tests/unit/test_registry.py::test_validator_rejects_zero_budget PASSED
tests/unit/test_registry.py::test_validator_accepts_mutating_tool_at_t3 PASSED
tests/unit/test_registry.py::test_validator_rejects_mutating_without_intent PASSED
tests/unit/test_registry.py::test_load_dir_rejects_duplicate_agent_id PASSED
tests/unit/test_registry.py::test_load_dir_rejects_t1_kubectl_scale_yaml PASSED
tests/unit/test_registry.py::test_load_dir_rejects_default_tier_t4_yaml PASSED
tests/unit/test_registry.py::test_store_get_and_tool_binding PASSED
tests/unit/test_registry.py::test_store_mirrors_to_sqlite PASSED
tests/unit/test_registry.py::test_store_hot_reload PASSED
tests/unit/test_registry.py::test_store_reload_drops_removed_agents PASSED

============================== 15 passed in 2.72s ==============================
```

## Design notes / deviations

1. **Mutating-tool heuristic**: validator treats `kubectl_*` (except `get/describe/logs`) and any tool name containing `_scale|_delete|_apply|_rollout|_destroy|_create|_patch` as mutating, requiring `requires_intent=True` AND `max_tier >= T3`. Errs on the side of false-positive — explicit allowlist for kubectl read verbs.
2. **`LoadedRegistry`**: kept as a thin typed `dict[str, AgentSpec]` subclass; not strictly necessary but gives callers a single import surface and `agent_ids()` helper.
3. **SQLite mirror schema**: stores both `spec_yaml` (operator-readable) and `spec_json` (machine-parseable) plus a few hot columns (`owner`, `default_tier`, budgets) for queryable dashboards without parsing JSON.
4. **`reload()` semantics**: clears in-memory dict then re-runs `load()`. UPSERT preserves removed agents in SQLite — they're dropped from the in-memory cache so `get()` returns None, but stale rows remain in the table. Acceptable for v1.0; v2 can soft-delete on reload.
5. **SIGHUP handler**: no-op on Windows (no `signal.SIGHUP`). Handler is a simple callback wrapper — actual reload wiring (calling `store.reload()`) is the caller's responsibility (will be done in server lifespan).
6. **Two extra tests beyond spec**: `test_store_reload_drops_removed_agents` and `test_validator_rejects_mutating_without_intent` — both directly exercise threat-model defenses (K4 sealed-tool exhaustiveness, registry hygiene).

## Imports
Reads `AgentSpec`, `AutonomyTier`, `TaskClassConfig`, `ToolBinding` from `acp.schemas.agent` as expected from W2A. No new schema additions needed.

## Total
- src: 371 LOC (target ~400)
- tests: 308 LOC (target ~200; the extra reflects 15 tests vs. 7 in spec)
- YAML: 70 LOC
