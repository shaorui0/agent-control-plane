# Plan A — SRE-Pragmatist v1.0 Implementation

**Author lens:** SRE-pragmatist. Ship a complete, deployable v1.0 a senior SRE can `pip install` and run tomorrow. Boring stack, single binary, no half-modules.

**LOC budget:** ~2500 LOC across ~30 files. Achievable in one session via parallel implementer agents.

**Non-goals (v1.0):** distributed deployment, multi-tenant auth, GUI, real LLM judge calls (stub + pluggable interface), Postgres (SQLite is fine), Kubernetes.

---

## 1. Tech Stack (opinionated, locked)

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | Type hints + `match` + `tomllib`; SRE-readable |
| Web framework | **FastAPI** + Uvicorn | Pydantic v2 validation = free schema enforcement at the gateway |
| Data validation | Pydantic v2 | One model = REST schema + DB row + YAML loader |
| Event store + state | **SQLite (WAL mode)** via `sqlite3` stdlib | Single file, zero infra, handles 10k events/s easily for v1.0 |
| Registry format | **YAML** (`pyyaml`) loaded at startup, hot-reload via SIGHUP | SREs already read YAML |
| Async / queue | **In-process `asyncio.Queue`** for judge pipeline + APScheduler for SLO eval cron | No Redis dep; queue is durable via SQLite outbox |
| CLI | **Typer** | `acp register|deploy|slo|burn|approve` |
| Testing | pytest + httpx.AsyncClient + freezegun | Standard |
| Logging | structlog → JSON to stdout | Wide events themselves go to SQLite, not logs |
| Packaging | `pyproject.toml` + `uv`-compatible | `pip install -e .` works |
| Process model | Single `uvicorn` process; APScheduler thread; asyncio judge worker | One binary, one port (8080) |

No Redis. No Kafka. No Postgres. No Docker required (works without). Dockerfile shipped but optional.

---

## 2. Module Breakdown

```
src/acp/
├── __init__.py
├── __main__.py              # python -m acp -> CLI
├── cli.py                   # Typer app: register/deploy/slo/burn/approve/serve
├── config.py                # Settings (pydantic-settings): DB path, registry dir, ports
├── server.py                # FastAPI app factory, lifespan, route mounting
│
├── registry/
│   ├── __init__.py
│   ├── models.py            # AgentSpec, ToolSpec, TaskClass, SLOTarget (Pydantic)
│   ├── loader.py            # load_registry(dir) -> dict[agent_id, AgentSpec]; SIGHUP reload
│   └── validator.py         # validate owner present, tools sealed, task_classes have SLO
│
├── gateway/                 # L1 Tool Gateway — the choke point
│   ├── __init__.py
│   ├── routes.py            # POST /v1/tool/invoke, /v1/event, /v1/task/start, /v1/task/end
│   ├── policy.py            # tier_check(), budget_check(), intent_check(), seal_check()
│   ├── budget.py            # per-agent token+$ counter (SQLite UPSERT)
│   └── tools.py             # ToolRegistry: dispatcher. Built-ins: shell_ro, http_get, kubectl_get, sql_select
│
├── sandbox/                 # L2 - thin; we just enforce step limit + fan-out helper
│   ├── __init__.py
│   ├── trajectory.py        # Trajectory ctx mgr: task_id, step counter, max_steps guard
│   └── fanout.py            # parallel_subagents(specs) helper (asyncio.gather + per-child trajectory)
│
├── events/                  # L3 Wide Event Store
│   ├── __init__.py
│   ├── schema.sql           # wide_events table (one wide row per event, JSON column for high-card attrs)
│   ├── store.py             # WideEventStore: emit(), query(sql/filter), tail()
│   └── sli.py               # SLI query builder: success_rate, p95_latency, intervention_rate per (task_class, model_version)
│
├── judge/                   # L4 Async judge pipeline
│   ├── __init__.py
│   ├── pipeline.py          # JudgeWorker: pulls unjudged events, runs judges, writes judgment rows
│   ├── judges.py            # BaseJudge ABC + RuleJudge (regex/JSON checks) + LLMJudgeStub (pluggable)
│   └── audit.py             # sample_for_human_audit(rate=0.05) -> writes audit_queue rows
│
├── slo/                     # L5 SLO Engine
│   ├── __init__.py
│   ├── engine.py            # eval_slo(task_class, window) -> BurnRate; runs every 60s via APScheduler
│   ├── burnrate.py          # multi-window (1h fast, 6h slow) burn rate math
│   └── alerts.py            # AlertSink: stdout, file, optional webhook
│
├── autonomy/                # L6 Earned Autonomy Gradient
│   ├── __init__.py
│   ├── controller.py        # AutonomyController: current δ-state per agent, transitions on burn/judgment
│   ├── states.py            # T0_shadow / T1_suggest / T2_approve / T3_execute_high_review / T4_autonomous
│   └── transitions.py       # auto_tier_down(agent, reason); manual promote via CLI
│
├── human/                   # L7 Human interface
│   ├── __init__.py
│   ├── approval.py          # T2 approval queue: list/approve/reject endpoints + CLI
│   └── dashboard.py         # FastAPI /dashboard returns HTML (jinja2 single template) — SLO board + audit queue + autonomy state
│
└── db.py                    # connect(path), migrate(), transactional ctx mgr
```

**Total: ~30 files.** No `utils.py` dumping ground. Each module owns one defense target from the survey.

---

## 3. Data Model (SQLite DDL — single source of truth)

```sql
-- L0 mirror of YAML for fast joins
CREATE TABLE agents (
  agent_id TEXT PRIMARY KEY,
  owner TEXT NOT NULL,            -- M4: named owner required
  version TEXT NOT NULL,
  model_version TEXT NOT NULL,    -- M1: SLO is keyed on this
  spec_yaml TEXT NOT NULL,        -- raw YAML for audit
  loaded_at INTEGER NOT NULL
);

-- L3 wide events — one row per "thing that happened"
CREATE TABLE wide_events (
  event_id TEXT PRIMARY KEY,      -- ulid
  ts INTEGER NOT NULL,            -- unix ms
  agent_id TEXT NOT NULL,
  task_id TEXT NOT NULL,          -- groups a trajectory
  task_class TEXT NOT NULL,
  model_version TEXT NOT NULL,
  step INTEGER NOT NULL,
  event_type TEXT NOT NULL,       -- task_start|tool_call|tool_result|task_end|judgment|intervention
  tool_name TEXT,
  tier_required TEXT,             -- T0..T4
  outcome TEXT,                   -- ok|denied|error|escalated
  intent TEXT,                    -- the # INTENT line for mutating ops
  attrs_json TEXT NOT NULL,       -- high-card freeform: prompts, args, latency_ms, tokens, $cost, etc
  -- indexes
  UNIQUE(event_id)
);
CREATE INDEX ix_events_task ON wide_events(task_id, step);
CREATE INDEX ix_events_class_model_ts ON wide_events(task_class, model_version, ts);

-- L1 budget counters (per agent, sliding window via periodic rollup)
CREATE TABLE budgets (
  agent_id TEXT NOT NULL,
  window_start INTEGER NOT NULL,  -- hour bucket
  tokens INTEGER DEFAULT 0,
  usd_micros INTEGER DEFAULT 0,   -- $ * 1e6, integer math
  tool_calls INTEGER DEFAULT 0,
  PRIMARY KEY(agent_id, window_start)
);

-- L4 judgments
CREATE TABLE judgments (
  judgment_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES wide_events(event_id),
  judge_name TEXT NOT NULL,
  verdict TEXT NOT NULL,          -- pass|fail|uncertain
  score REAL,
  reason TEXT,
  ts INTEGER NOT NULL
);
CREATE INDEX ix_judgments_event ON judgments(event_id);

-- human audit queue
CREATE TABLE audit_queue (
  audit_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  reason TEXT NOT NULL,           -- sample|escalation|disagreement
  status TEXT NOT NULL DEFAULT 'pending', -- pending|approved|rejected
  reviewer TEXT,
  reviewed_at INTEGER,
  notes TEXT
);

-- L5 SLO snapshots (one per eval tick per task_class)
CREATE TABLE slo_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  ts INTEGER NOT NULL,
  agent_id TEXT NOT NULL,
  task_class TEXT NOT NULL,
  model_version TEXT NOT NULL,
  window_label TEXT NOT NULL,     -- '1h'|'6h'|'30d'
  sli_value REAL NOT NULL,
  slo_target REAL NOT NULL,
  burn_rate REAL NOT NULL,
  budget_remaining REAL NOT NULL
);
CREATE INDEX ix_slo_recent ON slo_snapshots(agent_id, task_class, ts DESC);

-- L6 autonomy state
CREATE TABLE autonomy_state (
  agent_id TEXT NOT NULL,
  task_class TEXT NOT NULL,
  current_tier TEXT NOT NULL,     -- T0..T4
  since INTEGER NOT NULL,
  last_reason TEXT,
  PRIMARY KEY(agent_id, task_class)
);

-- L1 T2 approvals
CREATE TABLE approvals (
  approval_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  intent TEXT NOT NULL,
  args_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  decided_by TEXT,
  decided_at INTEGER
);
```

### Registry YAML schema (`registry/<agent_id>.yaml`)
```yaml
agent_id: payments-oncall-bot
owner: rshao@datavisor.com         # M4 — required, validated as email
version: 1.0.3
model_version: claude-opus-4-7-1m  # M1 — SLO keyed on this string
description: "Triages payments-api alerts"

task_classes:
  - name: triage_alert
    slo:
      sli: success_rate            # success_rate | intervention_free_rate | p95_latency_ms
      target: 0.95                 # 95% must pass judge
      window: 7d
      error_budget_policy: auto_tier_down
  - name: restart_pod
    slo:
      sli: intervention_free_rate
      target: 0.99
      window: 30d

sealed_tools:                      # K4 — exhaustive whitelist, no creds in agent
  - shell_ro
  - kubectl_get
  - http_get
  - kubectl_scale:
      tier: T2                     # requires human approval
      max_replicas_delta: 2
      requires_intent: true

budget:
  hourly_usd: 5.00
  hourly_tokens: 500000

autonomy:
  default_tier: T1_suggest
  promotion_policy:
    min_judged_pass: 100
    min_pass_rate: 0.97
```

---

## 4. API Surface (FastAPI, Tool Gateway is the choke point)

All agent → world traffic flows through `:8080`. Agents have **no other network egress** (enforced at deploy via netns/iptables, documented but not implemented in v1.0 — we document the constraint).

### Agent-facing (the gateway)

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/task/start` | Returns `task_id`. Body: `{agent_id, task_class, input}`. Creates trajectory + emits `task_start` event. |
| POST | `/v1/tool/invoke` | **The hot path.** Body: `{task_id, tool_name, args, intent}`. Runs: seal_check → tier_check → budget_check → intent_check → execute → emit events → return result OR `{status: pending_approval, approval_id}`. |
| POST | `/v1/task/end` | Body: `{task_id, final_output, outcome}`. Emits `task_end`, enqueues for judge. |
| POST | `/v1/event` | Agent emits structured observation/thought event (optional, encouraged for wide events). |
| GET | `/v1/task/{task_id}/approval/{approval_id}` | Poll for T2 approval verdict. |

### Operator-facing

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/registry` | List agents + current autonomy tier |
| POST | `/v1/registry/reload` | Hot reload YAML dir |
| GET | `/v1/slo?agent_id=&task_class=&window=` | Current SLI/burn rate |
| GET | `/v1/events?task_id=...` | Trajectory replay |
| GET | `/v1/approvals?status=pending` | T2 queue |
| POST | `/v1/approvals/{id}/decide` | `{decision: approve\|reject, reviewer, notes}` |
| GET | `/v1/audit?status=pending` | Human audit queue (sampled by L4) |
| POST | `/v1/audit/{id}/decide` | Submit audit verdict |
| POST | `/v1/autonomy/{agent_id}/{task_class}/promote` | Manual tier promote (CLI only, auth via shared secret) |
| GET | `/dashboard` | Single-page HTML — SLO board, burn rates, approval queue, audit queue |
| GET | `/healthz` `/readyz` | Standard |

### Python in-process API
For local dev / tests, `acp.client.LocalClient` bypasses HTTP and calls gateway functions directly. Same signature.

---

## 5. Test Plan (pytest, written alongside each module)

Order = bottom-up = implementation order.

1. **`tests/test_db.py`** — migrate creates all tables; transactional rollback works.
2. **`tests/test_registry.py`** — load valid YAML, reject missing owner (M4), reject ungated mutating tool, hot-reload.
3. **`tests/test_events_store.py`** — emit 10k events <1s; query by task_id; query by (task_class, model_version, window).
4. **`tests/test_sli.py`** — given fixture of 100 events with known outcomes + judgments, SLI returns expected success_rate.
5. **`tests/test_gateway_policy.py`** — sealed tool not in whitelist → denied; mutating tool without intent → denied; budget exhausted → denied; T2 tool → returns pending_approval.
6. **`tests/test_gateway_invoke.py`** (httpx) — full POST /v1/tool/invoke happy path + 4 denial paths.
7. **`tests/test_judge.py`** — RuleJudge fires on regex; sample rate 5% → audit_queue has ~5/100.
8. **`tests/test_slo_engine.py`** — burn rate math: 1h window 50% failures vs 95% target → burn=10x; auto_tier_down fires.
9. **`tests/test_autonomy.py`** — start T1; 100 judged pass → eligible promote; burn>5x → auto-demote.
10. **`tests/test_approval.py`** — T2 tool blocks until approval row decided; rejection denies invocation.
11. **`tests/test_e2e_demo.py`** — runs the demo agent (§6) end-to-end against in-proc server, asserts dashboard reflects state.

Coverage target: **80% line, 100% on `gateway/policy.py`** (the trust boundary).

---

## 6. Demo: `examples/payments_oncall_bot/`

A toy agent that demonstrates the **full 7-layer loop**.

**Scenario:** A fake Prometheus alert fires (`payments-api OOMKilled`). The agent must:
1. Investigate (T0 tools: `kubectl_get`, `shell_ro` to read logs) → emits wide events
2. Diagnose (LLM call stubbed — returns canned analysis)
3. Propose remediation (`kubectl_scale --replicas=1` — T2 tool, requires approval)
4. Wait for human approval via CLI
5. Execute on approval; emit task_end
6. Judge pipeline scores trajectory (RuleJudge: did it emit `# INTENT`? did it verify after scale?)
7. SLO engine updates; if 5 demo runs pass → autonomy controller promotes to T3 for `restart_pod` class

Files:
- `examples/payments_oncall_bot/agent.yaml` — registry entry
- `examples/payments_oncall_bot/run_demo.py` — driver script using `LocalClient`
- `examples/payments_oncall_bot/fake_cluster.py` — in-memory pod/scale state for `kubectl_*` tools
- `examples/payments_oncall_bot/README.md` — 60-second "run this" walkthrough

`make demo` runs it; final output is a screenshot of `/dashboard` showing burn rate, audit sample, autonomy promotion event.

---

## 7. Deliverable File List (exhaustive)

```
agent_control_plane/
├── pyproject.toml                              # deps, scripts, ruff config
├── README.md                                   # quickstart, architecture diagram, run demo
├── Makefile                                    # make install/test/demo/serve/lint
├── Dockerfile                                  # optional, single-stage python:3.11-slim
├── .gitignore
├── docs/
│   ├── architecture.md                         # 7-layer diagram + defense matrix (K1-M4)
│   ├── operator_runbook.md                     # how SRE deploys, reads dashboard, handles burn
│   ├── agent_author_guide.md                   # how to register a new agent
│   └── threat_model.md                         # K1-K4 + M1-M4 mapping to code
├── src/acp/
│   ├── __init__.py                             # version
│   ├── __main__.py                             # python -m acp -> cli.app()
│   ├── cli.py                                  # Typer: serve|register|slo|burn|approve|audit|promote
│   ├── config.py                               # pydantic-settings: ACP_DB_PATH, ACP_REGISTRY_DIR, etc.
│   ├── server.py                               # FastAPI app factory + lifespan (startup migrate, judge worker, scheduler)
│   ├── db.py                                   # sqlite3 conn pool, migrate(), tx context
│   ├── client.py                               # LocalClient (in-proc) + RemoteClient (httpx) — same iface
│   ├── ulid.py                                 # 26-char ULID generator (no dep)
│   ├── registry/
│   │   ├── __init__.py
│   │   ├── models.py                           # AgentSpec, ToolSpec, TaskClass, SLOTarget, BudgetSpec
│   │   ├── loader.py                           # load_dir() + hot-reload via SIGHUP
│   │   └── validator.py                        # M4 owner-required, K4 sealed tools, SLO required per task_class
│   ├── gateway/
│   │   ├── __init__.py
│   │   ├── routes.py                           # FastAPI router: /v1/task/* /v1/tool/* /v1/event
│   │   ├── policy.py                           # tier_check, budget_check, intent_check, seal_check (THE choke point)
│   │   ├── budget.py                           # hourly bucket UPSERT, get_remaining()
│   │   └── tools.py                            # ToolRegistry; builtins: shell_ro, http_get, kubectl_get, kubectl_scale, sql_select
│   ├── sandbox/
│   │   ├── __init__.py
│   │   ├── trajectory.py                       # K1: max_steps=20 default, emits step-bound events
│   │   └── fanout.py                           # K1: parallel_subagents helper, each gets fresh trajectory
│   ├── events/
│   │   ├── __init__.py
│   │   ├── schema.sql                          # ALL DDL (single source)
│   │   ├── store.py                            # emit(), query(), tail() — wide event API
│   │   └── sli.py                              # M3: query-based SLIs, no pre-aggregation
│   ├── judge/
│   │   ├── __init__.py
│   │   ├── pipeline.py                         # asyncio JudgeWorker, polls unjudged events
│   │   ├── judges.py                           # BaseJudge, RuleJudge (regex/JSONPath), LLMJudgeStub
│   │   └── audit.py                            # K3: sample_for_human_audit + disagreement detection
│   ├── slo/
│   │   ├── __init__.py
│   │   ├── engine.py                           # APScheduler 60s tick, writes slo_snapshots
│   │   ├── burnrate.py                         # multi-window (1h fast, 6h slow) math
│   │   └── alerts.py                           # AlertSink: stdout|file|webhook
│   ├── autonomy/
│   │   ├── __init__.py
│   │   ├── states.py                           # T0..T4 enum + invariants
│   │   ├── controller.py                       # current tier per (agent, task_class); reacts to SLO snapshots
│   │   └── transitions.py                      # auto_tier_down on burn; eligibility for promotion
│   └── human/
│       ├── __init__.py
│       ├── approval.py                         # /v1/approvals routes + CLI bridge
│       ├── dashboard.py                        # /dashboard FastAPI route, returns HTML
│       └── templates/
│           └── dashboard.html.j2               # single jinja2 template, vanilla CSS, no JS framework
├── tests/
│   ├── conftest.py                             # tmp DB, sample registry, freezegun fixture
│   ├── test_db.py
│   ├── test_registry.py
│   ├── test_events_store.py
│   ├── test_sli.py
│   ├── test_gateway_policy.py
│   ├── test_gateway_invoke.py
│   ├── test_judge.py
│   ├── test_slo_engine.py
│   ├── test_autonomy.py
│   ├── test_approval.py
│   └── test_e2e_demo.py
└── examples/
    └── payments_oncall_bot/
        ├── agent.yaml
        ├── run_demo.py
        ├── fake_cluster.py
        └── README.md
```

**File count: 52.** LOC estimate: 2400 source + 1100 tests ≈ 3500 total. Source alone ~2400 — within budget.

---

## 8. Implementation Order (dependency-correct)

Build in **5 waves**. Each wave is one parallelizable session block. Lower-numbered waves block higher.

**Wave 1 — Foundation (sequential, single agent, ~1h)**
1. `pyproject.toml`, `Makefile`, `db.py`, `config.py`, `ulid.py`, `events/schema.sql`
2. `tests/test_db.py` green

**Wave 2 — Registry + Event Store (parallel, 2 agents)**
- Agent 2A: `registry/` (models, loader, validator) + `test_registry.py`
- Agent 2B: `events/store.py` + `events/sli.py` + `test_events_store.py` + `test_sli.py`

**Wave 3 — Gateway (sequential, single agent, ~1.5h — this is the critical path)**
- `gateway/policy.py` first, fully tested in isolation → then `gateway/budget.py` → `gateway/tools.py` → `gateway/routes.py`
- `sandbox/trajectory.py` + `sandbox/fanout.py` (depends on gateway emit)
- All policy tests must be green before moving on

**Wave 4 — Judge + SLO + Autonomy (parallel, 3 agents)**
- Agent 4A: `judge/` + tests
- Agent 4B: `slo/` + tests
- Agent 4C: `autonomy/` + tests
- (They all read events, write back snapshots/judgments/state — no write conflicts)

**Wave 5 — Human + Server wiring + Demo (parallel, 2 agents)**
- Agent 5A: `human/approval.py` + `human/dashboard.py` + template + `cli.py` + `server.py` lifespan wiring
- Agent 5B: `examples/payments_oncall_bot/` + `test_e2e_demo.py` + `README.md` + `docs/`

**Gate at end of every wave:** `make test` green. No skipped tests. No `pass  # TODO`.

---

## 9. Risks / Open Questions

1. **SQLite write contention under judge worker + gateway hot path.** Mitigation: WAL mode + single writer thread + `BEGIN IMMEDIATE`. If we see lock errors in load test, switch to `litestream` or accept Postgres for v1.1. **Decision now:** stay on SQLite; document the ceiling (~1k events/s sustained).

2. **LLM judge is stubbed in v1.0.** RuleJudge + sampling is real, but `LLMJudgeStub` returns canned verdicts. Risk: demo looks weak. Mitigation: ship one real `AnthropicJudge` calling Sonnet 4.6 via `anthropic` SDK (gated behind env var `ACP_LLM_JUDGE_ENABLED`). Costs nothing if disabled.

3. **Network isolation of agents is documented but not enforced** in v1.0 (would require netns or container per agent). The choke-point property relies on operator deploying agents without other egress. **This is a documented operator responsibility**, called out in `operator_runbook.md`. v1.1 ships a `acp-runner` subprocess wrapper that enforces it.

4. **Hot-reload of YAML registry while tasks are in flight** — what tier applies, old or new? **Decision:** task carries `agent_version` at `task_start`; tier_check uses the version pinned at task start. Reload only affects new tasks.

5. **Clock skew + burn rate math** — APScheduler runs every 60s but events have ms timestamps from arbitrary clients. **Decision:** server stamps `ts` on event ingest, ignore client clock. Document this.

---

## Definition of Done (v1.0 ships when…)

- [ ] `pip install -e . && make demo` runs the payments_oncall_bot end-to-end on a fresh machine in <60s
- [ ] All 11 test files green; `gateway/policy.py` at 100% line coverage
- [ ] Dashboard at `localhost:8080/dashboard` shows: agent list, current tier per task_class, 1h/6h burn rates, pending approvals, pending audits
- [ ] `docs/threat_model.md` maps every defense target (K1, K2, K3, K4, M1, M3, M4) to a specific module and a specific test
- [ ] Single Python process, single SQLite file, no other infra required
- [ ] `acp --help` shows all 7 CLI subcommands and they all work

If any box is unchecked, it's not v1.0. No partial ships.
