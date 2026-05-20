# MASTER PLAN — Agent Control Plane (ACP) v1.0

**Date**: 2026-05-20
**Synthesized from**: plan_A_sre_pragmatist.md + plan_B_ai_native.md + plan_C_adversarial.md
**Target**: shippable v1.0, ~3500 LOC src + ~1500 LOC tests, single Python process, single SQLite file, no infra deps.

---

## 0. Design axioms (locked, non-negotiable)

1. **The agent is a suspect, not a user.** (from C) — every byte from agent is adversarial input.
2. **Deny by default.** Every policy/budget/Pydantic check fails closed. No "soft" defaults.
3. **One-way auditability.** Agent can cause events; cannot read/write/delete them.
4. **Outcome is derived, not reported.** Agent self-report is `trust=untrusted` data.
5. **Cross-model judge by default.** Judge model family ≠ agent model family. Hard-coded check.
6. **Wide events are the storage primitive.** SLI is a query, not a counter.
7. **Per-task-class × per-model-version SLO.** Never aggregate across versions (M1 defense).
8. **Earned Autonomy Gradient is a live daemon.** Auto-contracts on burn, auto-promotes on clean stretches.
9. **Tier is computed server-side; never self-attested.** Agent learns tier only by which tools work.
10. **Verdicts are mutable.** Outcome signals can retroactively flip a `pass` to `fail` (K2 defense).

## 1. Tech stack (LOCKED)

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | type hints + `match` + Pydantic v2 perf |
| Schemas | **Pydantic v2** everywhere (wire + DB + LLM structured outputs) | single source of truth |
| Web framework | **FastAPI + Uvicorn** | OpenAPI from Pydantic, async-native |
| Storage | **SQLite (WAL mode)** via stdlib `sqlite3` + `aiosqlite` for async paths | single file, zero infra |
| Migrations | hand-written SQL in `events/migrations/*.sql` | append-only event store demands explicit control |
| Registry format | **YAML** + Pydantic loader, SIGHUP hot reload | SRE-friendly |
| Async / queue | `asyncio.TaskGroup` + APScheduler for SLO eval cron | no Redis/Celery |
| LLM SDKs | `anthropic` (main agent) + `openai` (judge A) — both optional via env vars | cross-model judge |
| Crypto | `hashlib.blake2b` for event chain (no ed25519 sig in v1.0; documented as v2) | cheap chain integrity |
| CLI | **Typer** | `acp serve|register|slo|burn|approve|audit|promote|verify` |
| Logging | **structlog** → JSON stdout | wide events go to SQLite, NOT logs |
| Test | pytest + pytest-asyncio + httpx.AsyncClient + freezegun + hypothesis | hypothesis for schema fuzz |
| Lint | ruff + pyright strict | strict typing non-negotiable |
| Packaging | pyproject.toml + uv-compatible | `pip install -e .` works |
| Process | single Uvicorn + APScheduler thread + asyncio judge worker | one port (8080), one binary |

**Explicitly rejected**: Redis, Postgres (v1.0), Kafka, LangChain, gRPC, Docker (optional Dockerfile but not required).

## 2. Working directory

```
adhoc_jobs/agent_control_plane/
├── pyproject.toml
├── README.md                       # entry point
├── Makefile                        # make install/test/demo/serve/lint/verify
├── .env.example                    # ANTHROPIC_API_KEY, OPENAI_API_KEY (optional)
├── .gitignore
│
├── src/acp/
│   ├── __init__.py
│   ├── __main__.py                 # python -m acp → cli
│   ├── settings.py                 # pydantic-settings; reads .env
│   ├── clock.py                    # injectable Clock for tests
│   ├── ids.py                      # ULID, server-issued run_id nonce
│   ├── crypto.py                   # blake2b chain helpers
│   ├── errors.py                   # DenyClosed + typed errors (no-leak)
│   ├── db.py                       # sqlite3 conn pool, migrate(), tx ctx
│   ├── server.py                   # FastAPI app factory, lifespan wiring
│   ├── cli.py                      # Typer app
│   │
│   ├── schemas/                    # Pydantic v2 typed events (THE primitive)
│   │   ├── __init__.py
│   │   ├── base.py                 # BaseEvent
│   │   ├── agent.py                # AgentSpec, AutonomyTier, TaskClass
│   │   ├── tool.py                 # ToolSpec, ToolCallRequest, ToolCallResult, IntentProof
│   │   ├── decision.py             # AgentDecision
│   │   ├── judge.py                # JudgeVerdict, JudgePanelResult, GoodhartFlag
│   │   ├── slo.py                  # SLODefinition, BurnRateWindow, BudgetSnapshot
│   │   ├── outcome.py              # OutcomeSignal
│   │   ├── human.py                # ApprovalRequest, AuditFinding
│   │   ├── autonomy.py             # AutonomyTierChange
│   │   └── wide_event.py           # WideEvent discriminated union
│   │
│   ├── registry/                   # L0
│   │   ├── __init__.py
│   │   ├── models.py               # AgentSpec, ToolBinding, TaskClassConfig
│   │   ├── loader.py               # load_dir(), SIGHUP reload
│   │   ├── validator.py            # M4: owner required; K4: sealed tools exhaustive
│   │   └── store.py                # in-mem + SQLite mirror
│   │
│   ├── gateway/                    # L1 — THE choke point
│   │   ├── __init__.py
│   │   ├── routes.py               # FastAPI router /v1/* (the only door)
│   │   ├── auth.py                 # per-run bearer token, single-use scoped
│   │   ├── policy.py               # seal_check + tier_check + budget_check + intent_check
│   │   ├── intent_check.py         # validates intent string + optional LLM coherence check
│   │   ├── action_token.py         # signed short-TTL action token (T13)
│   │   ├── idempotency.py          # SERVER-issued keys; agent-supplied → reject (T12)
│   │   ├── egress_dlp.py           # outbound secret/entropy scanner (T15)
│   │   ├── budget.py               # token / $ / wall / step caps; UPSERT hourly buckets
│   │   ├── attestation.py          # Gateway emits Gateway-attested events; agent cannot forge
│   │   └── tools/                  # sealed tool implementations
│   │       ├── __init__.py
│   │       ├── base.py             # @sealed_tool decorator → ToolSpec + handler
│   │       ├── vm_query.py         # T1 read-only PromQL mock
│   │       ├── loki_query.py       # T1 read-only LogQL mock
│   │       ├── kubectl_get.py      # T1
│   │       ├── kubectl_describe.py # T1
│   │       ├── kubectl_scale.py    # T3 reversible-ish, requires approval
│   │       ├── kubectl_rollout.py  # T3
│   │       ├── slack_post.py       # T2 reversible
│   │       └── runbook_search.py   # T1
│   │
│   ├── sandbox/                    # L2 — thin in v1.0; enforces step + cost caps
│   │   ├── __init__.py
│   │   ├── trajectory.py           # max_steps default 20; emits step-bound events
│   │   ├── fanout.py               # parallel_subagents() helper; fresh trajectory per child
│   │   └── budgets.py              # raises BudgetExceeded
│   │
│   ├── events/                     # L3 — wide event store
│   │   ├── __init__.py
│   │   ├── migrations/
│   │   │   └── 0001_init.sql       # ALL DDL
│   │   ├── store.py                # WideEventStore: emit() with blake2b chain
│   │   ├── verifier.py             # offline chain integrity check (CLI)
│   │   ├── query.py                # read API for Judge (read-only cursor)
│   │   └── sli.py                  # M3 query-based SLI; no pre-aggregation
│   │
│   ├── judge/                      # L4
│   │   ├── __init__.py
│   │   ├── llm_clients.py          # AnthropicJudge + OpenAIJudge + StubJudge (no key)
│   │   ├── rubric.py               # JudgeVerdict Pydantic rubric
│   │   ├── pipeline.py             # async worker: pulls unjudged events; cross-model panel
│   │   ├── disagreement.py         # Cohen's κ; route to human on κ < 0.6
│   │   ├── goodhart.py             # 4 heuristic detectors (length / mismatch / self-cite / metric-local)
│   │   ├── adversarial.py          # CoT secondary judge (optional, T4)
│   │   ├── calibration.py          # 1-5% sample stratified by tier; drift alarm
│   │   └── replay.py               # /v1/judge/replay endpoint backend
│   │
│   ├── slo/                        # L5
│   │   ├── __init__.py
│   │   ├── engine.py               # APScheduler 60s tick; writes slo_snapshots
│   │   ├── burnrate.py             # multi-window (1h/6h/24h/7d) Honeycomb-style
│   │   ├── budget.py               # organic vs adversarial budget carve-out
│   │   ├── feedback.py             # OutcomeSignal → retroactive verdict flip
│   │   └── alerts.py               # AlertSink: stdout | file | webhook
│   │
│   ├── autonomy/                   # L6 — Earned Autonomy Gradient
│   │   ├── __init__.py
│   │   ├── states.py               # T0..T4 enum + tier definitions
│   │   ├── controller.py           # current δ-tier per (agent, task_class); reacts to SLO snapshots
│   │   ├── transitions.py          # auto_tier_down on burn; eligibility for promotion (asymmetric)
│   │   └── events.py               # AutonomyTierChange events
│   │
│   ├── human/                      # L7
│   │   ├── __init__.py
│   │   ├── approval.py             # T3/T4 approval queue endpoints
│   │   ├── audit.py                # calibration sample queue + audit findings
│   │   ├── pager.py                # owner pager (Slack webhook mock — NOT PagerDuty for SLO burn)
│   │   ├── dashboard.py            # /dashboard FastAPI route, returns HTML (jinja2)
│   │   └── templates/
│   │       └── dashboard.html.j2   # single template, vanilla CSS, no JS framework
│   │
│   └── sdk/                        # Python SDK for agents (the demo uses this)
│       ├── __init__.py
│       ├── client.py               # ACPClient + LocalClient (in-proc) + RemoteClient (httpx)
│       ├── agent_loop.py           # opinionated loop: think → tool → observe → decide
│       └── models.py               # re-exports schemas the agent code needs
│
├── agents/                         # YAML registry
│   ├── oncall_triage.yaml
│   └── code_reviewer.yaml          # second agent so cross-task-class SLO meaningful
│
├── demo/
│   ├── README.md
│   ├── run_demo.sh                 # boots mocks + ACP + runs 4 scenarios
│   ├── oncall_agent.py             # demo agent using SDK
│   ├── mock_vm_server.py           # tiny FastAPI mimicking VictoriaMetrics
│   ├── mock_loki_server.py
│   ├── fake_cluster.py             # in-memory pod state for kubectl tools
│   └── scenarios/
│       ├── 01_cpu_spike.json
│       ├── 02_silent_fail.json     # K2: declares success while metric never recovered
│       ├── 03_goodhart_inflation.json  # K3: verbose CoT to game judge
│       └── 04_irreversible_temptation.json  # K4: tempt scale-to-zero on prod
│
├── docs/
│   ├── ARCHITECTURE.md             # 7-layer diagram + sequence diagrams
│   ├── THREAT_MODEL.md             # K1-K4 / M1-M4 + 15 threats → modules
│   ├── INVARIANTS.md               # SI-1..SI-13 with code line refs
│   ├── OPERATOR_RUNBOOK.md         # how SRE deploys, reads dashboard, handles burn, chain break
│   └── AGENT_AUTHOR_GUIDE.md       # how to register a new agent
│
├── tests/
│   ├── conftest.py                 # tmp DB, sample registry, freezegun, in-proc client
│   ├── unit/
│   │   ├── test_db.py
│   │   ├── test_registry.py
│   │   ├── test_events_store.py
│   │   ├── test_events_chain.py    # blake2b integrity
│   │   ├── test_sli.py
│   │   ├── test_gateway_policy.py
│   │   ├── test_gateway_invoke.py
│   │   ├── test_intent_check.py
│   │   ├── test_idempotency.py
│   │   ├── test_action_token.py
│   │   ├── test_egress_dlp.py
│   │   ├── test_judge_pipeline.py
│   │   ├── test_goodhart.py
│   │   ├── test_calibration.py
│   │   ├── test_slo_engine.py
│   │   ├── test_burnrate.py
│   │   ├── test_outcome_feedback.py
│   │   ├── test_autonomy.py
│   │   └── test_approval.py
│   ├── integration/
│   │   ├── test_e2e_happy_path.py
│   │   ├── test_e2e_silent_fail.py  # K2 retroactive flip end-to-end
│   │   ├── test_e2e_goodhart.py
│   │   └── test_e2e_irreversible.py
│   └── adversarial/                # red-team gauntlet (must pass before ship)
│       ├── conftest.py             # DeceptiveAgent simulator
│       ├── test_A01_outcome_lying.py
│       ├── test_A02_synthetic_data.py
│       ├── test_A03_tier_leak.py
│       ├── test_A04_event_store_isolation.py
│       ├── test_A05_idempotency_collision.py
│       ├── test_A06_action_token_binding.py
│       ├── test_A07_egress_dlp.py
│       ├── test_A08_prompt_injection.py
│       ├── test_A09_judge_manipulation.py
│       ├── test_A10_budget_burst.py
│       ├── test_A11_chain_tamper.py
│       └── test_A12_eval_awareness.py
│
├── blog/
│   └── agent_control_plane_design.md   # post-build essay; thinking + trade-offs
│
├── evidence/                       # demo HTML reports, screenshots
│
└── planning/
    ├── plan_A_sre_pragmatist.md
    ├── plan_B_ai_native.md
    ├── plan_C_adversarial.md
    └── MASTER_PLAN.md              # THIS FILE
```

**File count**: ~75 (50 source + 25 tests/docs/demo). LOC ~3500 src + ~1500 tests.

## 3. Data model (SQLite DDL — single source of truth in `events/migrations/0001_init.sql`)

```sql
CREATE TABLE agents (
  agent_id TEXT PRIMARY KEY,
  owner TEXT NOT NULL,           -- M4: named owner required (email)
  version TEXT NOT NULL,
  model_version TEXT NOT NULL,   -- M1: SLO keyed on this
  spec_yaml TEXT NOT NULL,
  loaded_at INTEGER NOT NULL
);

CREATE TABLE wide_events (
  event_id TEXT PRIMARY KEY,       -- ULID
  prev_event_id TEXT,              -- chain pointer for this run
  ts INTEGER NOT NULL,             -- unix ms, server-stamped
  run_id TEXT NOT NULL,            -- server-issued nonce, groups trajectory
  agent_id TEXT NOT NULL,
  task_class TEXT NOT NULL,
  model_version TEXT NOT NULL,
  step INTEGER NOT NULL,
  event_type TEXT NOT NULL,        -- task_start | tool_call | tool_result | task_end | judgment | intervention | outcome | autonomy_change
  tool_name TEXT,
  tier_required TEXT,              -- T0..T4
  outcome TEXT,                    -- ok | denied | error | escalated | pending
  intent TEXT,                     -- # INTENT line for mutating ops
  agent_claim TEXT,                -- agent's narrative; trust=untrusted
  attrs_json TEXT NOT NULL,        -- high-cardinality freeform
  chain_hash TEXT NOT NULL         -- blake2b(prev_hash || canonical(payload))
);
CREATE INDEX ix_events_run ON wide_events(run_id, step);
CREATE INDEX ix_events_class_model_ts ON wide_events(task_class, model_version, ts);
CREATE INDEX ix_events_agent_ts ON wide_events(agent_id, ts);

CREATE TABLE budgets (
  agent_id TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  tokens INTEGER DEFAULT 0,
  usd_micros INTEGER DEFAULT 0,
  tool_calls INTEGER DEFAULT 0,
  PRIMARY KEY(agent_id, window_start)
);

CREATE TABLE judgments (
  judgment_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES wide_events(event_id),
  judge_name TEXT NOT NULL,
  judge_model TEXT NOT NULL,       -- specific model used (different from agent's)
  verdict TEXT NOT NULL,           -- pass | fail | uncertain | escalate
  rubric_json TEXT NOT NULL,       -- correctness/grounding/safety/deception_risk/goodhart_risk
  rationale TEXT,
  ts INTEGER NOT NULL,
  retroactively_flipped INTEGER DEFAULT 0
);
CREATE INDEX ix_judgments_event ON judgments(event_id);

CREATE TABLE goodhart_flags (
  flag_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  signal TEXT NOT NULL,            -- length_anomaly | reasoning_action_mismatch | self_citation | metric_local
  evidence_json TEXT NOT NULL,
  ts INTEGER NOT NULL
);

CREATE TABLE outcome_signals (
  signal_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,              -- git_applied | oncall_refire | csat_proxy | cost_delta | rollback_required
  value_json TEXT NOT NULL,
  delay_seconds INTEGER NOT NULL,
  source TEXT NOT NULL,
  ts INTEGER NOT NULL
);
CREATE INDEX ix_outcome_run ON outcome_signals(run_id);

CREATE TABLE slo_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  ts INTEGER NOT NULL,
  agent_id TEXT NOT NULL,
  task_class TEXT NOT NULL,
  model_version TEXT NOT NULL,
  window_label TEXT NOT NULL,      -- 1h | 6h | 24h | 7d
  budget_class TEXT NOT NULL,      -- organic | adversarial
  sli_value REAL NOT NULL,
  slo_target REAL NOT NULL,
  burn_rate REAL NOT NULL,
  budget_remaining REAL NOT NULL
);
CREATE INDEX ix_slo_recent ON slo_snapshots(agent_id, task_class, ts DESC);

CREATE TABLE autonomy_state (
  agent_id TEXT NOT NULL,
  task_class TEXT NOT NULL,
  current_tier TEXT NOT NULL,      -- T0..T4
  since INTEGER NOT NULL,
  last_reason TEXT,
  PRIMARY KEY(agent_id, task_class)
);

CREATE TABLE approvals (
  approval_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  intent TEXT NOT NULL,
  args_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
  decided_by TEXT,
  decided_at INTEGER
);

CREATE TABLE audit_queue (
  audit_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  reason TEXT NOT NULL,            -- sample | escalation | disagreement | goodhart_flag
  status TEXT NOT NULL DEFAULT 'pending',
  reviewer TEXT,
  reviewed_at INTEGER,
  notes TEXT,
  human_label TEXT                 -- for calibration
);

CREATE TABLE calibration (
  cal_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  judge_panel_label TEXT NOT NULL,
  human_label TEXT NOT NULL,
  delta INTEGER NOT NULL,          -- 0 if agree, 1 if disagree
  judge_model TEXT NOT NULL,
  task_class TEXT NOT NULL,
  ts INTEGER NOT NULL
);

CREATE TABLE action_tokens (
  token_id TEXT PRIMARY KEY,       -- hash of token; raw never stored
  run_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  args_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  consumed_at INTEGER
);
```

## 4. API surface

### Agent-facing (`/v1/*`) — Tool Gateway

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/sessions` | Start a run. Body: `{agent_id, task_class, input}`. Returns `run_id` + session bearer. Emits `task_start`. |
| POST | `/v1/sessions/{run_id}/decisions` | Agent posts an `AgentDecision` (structured output). Pre-action log. |
| POST | `/v1/sessions/{run_id}/tools/{name}/invoke` | **The hot path.** Body validates against tool's Pydantic schema + must include `intent`. Returns result OR `{status:"pending_approval", approval_id}`. |
| POST | `/v1/sessions/{run_id}/end` | Body: `{final_output, agent_claim_outcome}`. Emits `task_end`. Enqueues judge. |
| GET | `/v1/sessions/{run_id}/approvals/{id}` | Poll approval status. |

### Operator-facing

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/agents` | list registry + current tier per task_class |
| POST | `/v1/registry/reload` | SIGHUP equivalent |
| GET | `/v1/slo?agent_id=&task_class=&window=` | current SLI/burn rate |
| GET | `/v1/trace/{run_id}` | full wide-event timeline |
| GET | `/v1/approvals?status=pending` | T3/T4 queue |
| POST | `/v1/approvals/{id}/decide` | `{decision, reviewer, notes}` |
| GET | `/v1/audit?status=pending` | calibration sample queue |
| POST | `/v1/audit/{id}/decide` | submit calibration verdict |
| POST | `/v1/judge/replay` | re-judge a historical run (anti-Goodhart) |
| POST | `/v1/outcomes` | external systems post `OutcomeSignal` |
| POST | `/v1/autonomy/{agent_id}/{task_class}/promote` | manual promote (CLI only) |
| GET | `/dashboard` | single-page HTML — SLO board + approvals + audits + autonomy state |
| GET | `/healthz` `/readyz` | std |

### Python SDK
`acp.sdk.LocalClient` (in-proc for tests) and `RemoteClient` (httpx). Same surface.

## 5. Implementation waves (dependency-ordered, parallelizable within wave)

**Wave 1 — Foundations** (1 agent, sequential)
- `pyproject.toml`, `Makefile`, `.gitignore`, `.env.example`, `README.md` skeleton
- `src/acp/__init__.py`, `settings.py`, `clock.py`, `ids.py`, `crypto.py`, `errors.py`, `db.py`
- `events/migrations/0001_init.sql`
- `tests/unit/test_db.py` green

**Wave 2 — Schemas + Event Store + Registry** (3 parallel agents)
- 2A: `schemas/*` (all Pydantic models) + hypothesis round-trip tests
- 2B: `events/store.py` + `events/verifier.py` + `events/query.py` + `events/sli.py` + tests (`test_events_store.py`, `test_events_chain.py`, `test_sli.py`)
- 2C: `registry/*` + `agents/*.yaml` + `test_registry.py`

**Wave 3 — Gateway (critical path)** (1 agent, sequential)
- `gateway/policy.py` first, fully tested → `gateway/auth.py` → `gateway/intent_check.py` → `gateway/idempotency.py` → `gateway/action_token.py` → `gateway/egress_dlp.py` → `gateway/budget.py` → `gateway/attestation.py` → `gateway/routes.py` → `gateway/tools/*`
- `sandbox/trajectory.py` + `sandbox/fanout.py` + `sandbox/budgets.py`
- Tests: `test_gateway_policy.py`, `test_gateway_invoke.py`, `test_intent_check.py`, `test_idempotency.py`, `test_action_token.py`, `test_egress_dlp.py`
- **Gate**: `gateway/policy.py` at 100% line coverage

**Wave 4 — Judge + SLO + Autonomy** (3 parallel agents)
- 4A: `judge/*` (llm_clients with StubJudge default + AnthropicJudge + OpenAIJudge, rubric, pipeline, disagreement, goodhart, adversarial, calibration, replay) + tests
- 4B: `slo/*` (engine, burnrate, budget, feedback, alerts) + tests including outcome retroactive-flip
- 4C: `autonomy/*` (states, controller, transitions, events) + tests

**Wave 5 — Human + SDK + Server wiring + Demo + Docs** (3 parallel agents)
- 5A: `human/*` (approval, audit, pager, dashboard, template) + `cli.py` + `server.py` lifespan wiring
- 5B: `sdk/*` + `demo/*` (oncall_agent + mock servers + 4 scenarios + run_demo.sh) + integration tests + adversarial tests
- 5C: `docs/*` (ARCHITECTURE.md, THREAT_MODEL.md, INVARIANTS.md, OPERATOR_RUNBOOK.md, AGENT_AUTHOR_GUIDE.md) + top-level README.md + `blog/agent_control_plane_design.md`

**Each wave's gate**: `make test` green, no skipped tests, no `pass # TODO`. Subsequent waves cannot start until prior wave gates pass.

## 6. Definition of Done

- [ ] `pip install -e . && make demo` runs all 4 demo scenarios end-to-end on a fresh machine in <2 min
- [ ] All unit + integration + adversarial tests green; `gateway/policy.py` 100% line coverage
- [ ] `acp verify --db <path>` confirms event chain integrity (A11 test)
- [ ] Dashboard at `localhost:8080/dashboard` shows: agent list, autonomy tier per task_class, 1h/6h burn rates, pending approvals + audits
- [ ] `docs/THREAT_MODEL.md` maps every K1-K4 + M1-M4 to a specific module and test
- [ ] Single Python process, single SQLite file, no other infra
- [ ] `acp --help` shows all CLI subcommands and they work
- [ ] Top-level README has 60-second quickstart that actually works
- [ ] Blog post draft exists in `blog/`

## 7. Risks (acknowledged, decisions locked)

1. **SQLite contention**: WAL + single writer thread + `BEGIN IMMEDIATE` for the chain-tail. Ceiling ~1k events/s sustained — documented in OPERATOR_RUNBOOK.
2. **Real LLM judges optional**: `StubJudge` (deterministic rules) is the default; `AnthropicJudge` + `OpenAIJudge` enabled only if API keys present. Demo works without keys.
3. **Network isolation documented not enforced**: agent process isolation requires netns/cgroups out of scope for v1.0. Operator runbook clarifies the responsibility.
4. **Hot-reload + in-flight tasks**: task pins `agent_version` at `task_start`; tier_check uses pinned version; reload only affects new runs.
5. **Clock skew**: server stamps `ts` on ingest; client clocks ignored.

## 8. Defense matrix (for THREAT_MODEL.md)

| Concern | Module(s) defending |
|---|---|
| K1 compounding | `slo/burnrate.py` + `autonomy/controller.py` + `sandbox/budgets.py` step caps |
| K2 silent failure | `slo/feedback.py` retroactive verdict flip + `OutcomeSignal` ingestion + `judge/calibration.py` drift detection |
| K3 Goodhart | `judge/goodhart.py` (4 heuristics) + `judge/calibration.py` (human sample) + `judge/disagreement.py` quorum + adversarial budget carve-out |
| K4 deceptive alignment | `gateway/policy.py` sealed tools + `gateway/intent_check.py` + `judge/adversarial.py` CoT secondary judge + cross-model judge invariant |
| M1 non-stationarity | `schemas/base.py` `model_version` required + `slo/sli.py` per-version SLI |
| M2 adversarial budget | `slo/budget.py` carve-out + Goodhart-flagged failures burn adversarial only |
| M3 obs primitives | Wide-event store + Pydantic events as primitive (no metric counters) |
| M4 responsibility gap | `registry/validator.py` owner-required + `human/pager.py` owner pager (not PD rotation) |
| T2 fabrication (Replit 4000) | `gateway/attestation.py` Gateway-derived `rows_affected`, agent_claim untrusted |
| T3 outcome lying | Outcome derived in `slo/feedback.py`, agent_claim mismatch → Goodhart flag |
| T4 judge manipulation | `judge/adversarial.py` CoT scoring + cross-model judge |
| T7 event tampering | `events/store.py` blake2b chain + `events/verifier.py` offline check |
| T11 cost bomb | `gateway/budget.py` hard caps + `sandbox/budgets.py` step caps |
| T12 idempotency replay | `gateway/idempotency.py` server-issued keys only |
| T13 TOCTOU | `gateway/action_token.py` short-TTL signed tokens binding args_hash |
| T15 side-channel | `errors.py` no-leak error class + `gateway/egress_dlp.py` outbound scanner |

---

## Master plan locked. Implementation phase begins.
