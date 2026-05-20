# Wave 5A — Human Interface + Server Lifespan + CLI

## Schema-collision resolution

**Decision: `RegistryStore._DDL` is the single source of truth for the `agents` table.**

The migration in `events/migrations/0001_init.sql` formerly defined a narrow
6-column placeholder (`agent_id, owner, version, model_version, spec_yaml,
loaded_at`). `RegistryStore.__init__` then re-created the same table name with
a richer 10-column schema (adds `default_tier`, `budget_hourly_usd`,
`budget_hourly_tok`, `spec_json`). Because both used `CREATE TABLE IF NOT
EXISTS`, whichever ran first won — and the gateway invoke tests were already
working around this with a `DROP TABLE IF EXISTS agents` hack.

Fix applied:
- `src/acp/events/migrations/0001_init.sql`: removed the `agents` CREATE TABLE
  block; left a comment explaining the ownership.
- `tests/unit/test_gateway_invoke.py`: removed the `DROP TABLE` workaround.
- `tests/unit/test_db.py`: removed `agents` from `EXPECTED_TABLES`; rewrote
  transaction commit/rollback tests to use the migration-owned `budgets` table
  instead.

Net effect: `RegistryStore.__init__` is the single place that creates the
`agents` table, and it always wins because it runs after `db.migrate()`.

## Files added

src/acp:
- `human/__init__.py`
- `human/approval.py` — `ApprovalQueue` + `/v1/approvals` routes + `/approvals` HTML
- `human/audit.py` — `AuditQueue` + `/v1/audit` routes + `/audit` HTML
- `human/pager.py` — `OwnerPager` + `PagerAlertSink` (AlertSink adapter)
- `human/dashboard.py` — `/dashboard` HTML route + data gatherer
- `human/templates/dashboard.html.j2`
- `human/templates/approvals.html.j2`
- `human/templates/audit.html.j2`
- `server.py` — `create_app(settings=...)` factory + lifespan + module-level `app`
- `cli.py` — Typer `acp` CLI: `serve`, `register`, `slo`, `burn`, `approve`,
  `audit list/decide`, `autonomy promote`, `verify`, `dashboard`

Settings (`src/acp/settings.py`) extended with `slack_webhook_url`,
`session_secret`, `dashboard_refresh_seconds`.

## Tests added (35 tests across 5 files)

- `tests/unit/test_approval.py` — 8 tests: list/decide/HTTP + intervention
  wide_event emission with mapped outcome (`approved`→`ok`+attrs.decision,
  `rejected`→`denied`+attrs.decision; necessary because `WideEvent.outcome` is
  a closed Literal).
- `tests/unit/test_audit.py` — 6 tests: submit persists calibration row, delta
  computation, HTTP route.
- `tests/unit/test_dashboard.py` — 2 tests: HTML renders with agent name +
  tier visible, queue counts shown.
- `tests/unit/test_cli.py` — 12 tests: each subcommand via Typer `CliRunner`,
  asserting exit codes + output structure.
- `tests/unit/test_server_lifespan.py` — 3 tests: `create_app()` startup runs
  migrations + registry load + autonomy_state init, gateway uses real
  `AutonomyController` (not `DefaultAutonomyProvider`), all routers mounted.

## Test results

```
$ pytest tests/unit/test_approval.py tests/unit/test_audit.py \
        tests/unit/test_dashboard.py tests/unit/test_cli.py \
        tests/unit/test_server_lifespan.py -xvs
... 31 passed in 1.23s
```

Full project suite: **310 passed, 0 failed**.

Note: an earlier run produced one flake
(`tests/adversarial/test_A11_chain_tamper.py::test_a11_attrs_mutation_detected`
with `sqlite3.OperationalError: near "LIMIT"`); it passes deterministically on
its own and on re-run of the full suite. Pre-existing order-dependent flake,
unrelated to W5A.

## Key design choices

1. **AutonomyController as AutonomyProvider**: the server lifespan wires the
   live `AutonomyController` into `GatewayDeps.autonomy`, fully replacing
   `DefaultAutonomyProvider`. No change needed to `routes.py` since
   `AutonomyController.current_tier` already matches the Protocol.
2. **Intervention outcome mapping**: WideEvent.outcome's allowed values are
   `ok|denied|error|escalated|pending`. We map approve→`ok`/reject→`denied`
   and stash the verbatim decision in `attrs.decision`. Downstream SLI/judge
   consumers read attrs.decision; the audit log preserves the human-readable
   verb.
3. **Templates packaged as data**: `pyproject.toml` already lists
   `human/templates/*.j2` under `package-data` so they ship with the wheel.
4. **Scheduler optional under sqlite `:memory:`**: tests using
   `TestClient(create_app(...))` would otherwise hang on APScheduler thread
   shutdown. The startup skips scheduler init when `db_path == :memory:`.
5. **CLI uses short-lived connections**: every subcommand opens its own
   SQLite connection against `settings.db_path`. WAL mode makes this safe
   while a server is running.
