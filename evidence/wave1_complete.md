# Wave 1 — Foundations: COMPLETE

Date: 2026-05-20
Agent: W1 (Implementation)

## Files created

| Path | LOC |
|---|---|
| `pyproject.toml` | 59 |
| `Makefile` | 25 |
| `.gitignore` | 17 |
| `.env.example` | 6 |
| `README.md` | 14 |
| `src/acp/__init__.py` | 1 |
| `src/acp/settings.py` | 52 |
| `src/acp/clock.py` | 38 |
| `src/acp/ids.py` | 54 |
| `src/acp/crypto.py` | 47 |
| `src/acp/errors.py` | 37 |
| `src/acp/db.py` | 56 |
| `src/acp/events/__init__.py` | 30 (overwritten by Wave 2 helper; see deviation) |
| `src/acp/events/migrations/0001_init.sql` | 140 |
| `tests/__init__.py` | 0 |
| `tests/unit/__init__.py` | 0 |
| `tests/conftest.py` | 46 |
| `tests/unit/test_db.py` | 69 |
| `tests/unit/test_ids.py` | 42 |
| `tests/unit/test_crypto.py` | 57 |
| `tests/unit/test_errors.py` | 40 |
| **Total** | **830** |

Every src file is under the 200-LOC ceiling.

## Install verification

`pip install -e ".[dev]"` (inside `.venv`) resolved cleanly. All declared
deps installed: fastapi 0.136, pydantic 2.13, sqlalchemy 2.0, aiosqlite 0.22,
typer 0.25, structlog 25.5, apscheduler 3.11, httpx 0.28, cbor2 6.1, ruff 0.15,
pyright 1.1, pytest 9.0, hypothesis 6.152, freezegun 1.5.

## pytest output (last 30 lines)

```
============================= test session starts ==============================
platform darwin -- Python 3.13.3, pytest-9.0.3, pluggy-1.6.0
configfile: pyproject.toml
plugins: asyncio-1.3.0, hypothesis-6.152.9, anyio-4.13.0
asyncio: mode=Mode.AUTO

collected 22 items

tests/unit/test_db.py::test_migrate_creates_all_tables PASSED
tests/unit/test_db.py::test_wal_mode_and_foreign_keys PASSED
tests/unit/test_db.py::test_transaction_commits PASSED
tests/unit/test_db.py::test_transaction_rolls_back PASSED
tests/unit/test_db.py::test_migrate_idempotent PASSED
tests/unit/test_ids.py::test_ulid_length_and_alphabet PASSED
tests/unit/test_ids.py::test_ulid_uniqueness_10k PASSED
tests/unit/test_ids.py::test_ulid_monotonic_within_same_ms PASSED
tests/unit/test_ids.py::test_ulid_monotonic_across_calls_real_time PASSED
tests/unit/test_ids.py::test_args_hash_canonical PASSED
tests/unit/test_ids.py::test_args_hash_changes_on_value_change PASSED
tests/unit/test_crypto.py::test_chain_hash_deterministic PASSED
tests/unit/test_crypto.py::test_chain_hash_depends_on_prev PASSED
tests/unit/test_crypto.py::test_verify_chain_clean PASSED
tests/unit/test_crypto.py::test_verify_chain_detects_payload_tamper PASSED
tests/unit/test_crypto.py::test_verify_chain_detects_hash_tamper PASSED
tests/unit/test_crypto.py::test_chain_hash_golden_vector PASSED
tests/unit/test_errors.py::test_deny_closed_carries_reason_code PASSED
tests/unit/test_errors.py::test_deny_closed_agent_dict_hides_internal_message PASSED
tests/unit/test_errors.py::test_deny_closed_default_message PASSED
tests/unit/test_errors.py::test_integrity_error_is_acp_error PASSED
tests/unit/test_errors.py::test_budget_exceeded_fields PASSED

============================== 22 passed in 0.16s ==============================
```

## Deviations from plan

1. **`src/acp/events/__init__.py`** was overwritten externally to re-export
   Wave 2 modules (`WideEventStore`, `EventQuery`, etc.) that Wave 1 does
   not produce. The Wave 1 unit tests do not import from this package, so
   this does not affect the Wave 1 gate. Wave 2 agent must produce the
   referenced `store.py`, `query.py`, `verifier.py`, `sli.py` for `acp.events`
   to import cleanly at package level.
2. **Python 3.13** used locally for verification (project requires 3.11+).
   Compatible.
3. **`cbor2` is available**, so `crypto._canonical` uses CBOR canonical
   encoding; the JSON fallback path is still present but only exercised if
   cbor2 is missing.
4. **`settings.py`** uses an explicit `_env_map` + `from_env` rather than
   `env_prefix="ACP_"` because the two LLM keys (`ANTHROPIC_API_KEY`,
   `OPENAI_API_KEY`) don't share the prefix. `get_settings()` is `lru_cache`d.
5. **`Settings.db_path` / `registry_dir`** are `Path`; pydantic coerces from
   string env vars automatically.

## Wave 1 gate: GREEN

- `pip install -e ".[dev]"` succeeds.
- All 22 Wave 1 unit tests pass.
- All 17 deliverable files produced, all under 200 LOC.
- No files written outside the assigned scope.
