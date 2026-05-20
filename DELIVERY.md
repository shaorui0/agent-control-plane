# Agent Control Plane (ACP) v1.0 — Delivery

**Delivered**: 2026-05-20
**Project root**: `adhoc_jobs/agent_control_plane/`

## What was built

A complete, working **Agent Control Plane** — a Kubernetes-style governance layer for AI agents in production. Implements the 7-layer architecture from the design discussion + 10 design axioms from MASTER_PLAN.md.

### Verification (all green)

| Check | Result |
|---|---|
| Full test suite | **310 passed, 0 failed**, 2.33s |
| `gateway/policy.py` coverage | **100% line coverage** (security choke point) |
| Demo (`bash demo/run_demo.sh`) | **ALL GREEN** — 4 scenarios + 17 integration/adversarial tests |
| Cross-model judge invariant | enforced at `judge/pipeline.py` init |
| Event chain integrity | blake2b-chained, `acp verify` CLI confirms |
| Project layout | 110 source files + 32 test files + 6 docs + 1 blog |

### Scale

- **Source code**: 8,372 LOC across `src/acp/` (50 modules)
- **Tests**: 5,614 LOC across unit / integration / adversarial
- **Documentation**: 1,728 LOC across docs/ + blog/
- **Total**: ~15,700 LOC

### File inventory

```
agent_control_plane/
├── README.md, pyproject.toml, Makefile, .env.example
├── src/acp/
│   ├── settings, clock, ids, crypto, errors, db, server, cli
│   ├── schemas/     (11 Pydantic v2 modules)
│   ├── registry/    (loader, validator, store + 2 agent YAMLs)
│   ├── gateway/     (policy, auth, intent_check, idempotency, action_token, egress_dlp, budget, attestation, routes + 8 sealed tools)
│   ├── sandbox/     (trajectory, fanout, budgets)
│   ├── events/      (store, verifier, query, sli + migrations/0001_init.sql)
│   ├── judge/       (llm_clients, rubric, pipeline, disagreement, goodhart, adversarial, calibration, replay)
│   ├── slo/         (definitions, burnrate, budget, feedback, engine, alerts)
│   ├── autonomy/    (states, controller, transitions, events)
│   ├── human/       (approval, audit, pager, dashboard + 3 Jinja2 templates)
│   └── sdk/         (client/LocalClient/RemoteClient, agent_loop, models)
├── agents/          (oncall_triage.yaml, code_reviewer.yaml)
├── demo/            (oncall_agent.py + 4 scenarios + mock VM/Loki + run_demo.sh)
├── docs/            (ARCHITECTURE, THREAT_MODEL, INVARIANTS, OPERATOR_RUNBOOK, AGENT_AUTHOR_GUIDE)
├── blog/            (agent_control_plane_design.md — 463-line essay)
├── tests/           (unit/ 240+ tests, integration/ 4 e2e, adversarial/ 12 A0x)
├── planning/        (3 source plans + MASTER_PLAN.md)
└── evidence/        (wave1..5 completion reports)
```

## How to use

```bash
cd adhoc_jobs/agent_control_plane

# install
make install              # pip install -e .[dev]

# run all tests
make test                 # 310 tests, 2.3s

# run the demo (4 scenarios + adversarial gauntlet)
make demo                 # ~5 seconds

# start the server + dashboard
make serve                # uvicorn on :8080
open http://localhost:8080/dashboard

# CLI
acp register --file agents/new_agent.yaml
acp slo --agent oncall-triage-agent --task-class triage_alert
acp burn --agent oncall-triage-agent
acp approve <approval_id> --reviewer rshao@datavisor.com
acp autonomy promote oncall-triage-agent triage_alert --tier T3 --operator rshao@datavisor.com
acp verify --db acp.db                  # event chain integrity check
```

## What it defends against (mapped to design discussion)

| Kill-level reframe from research | Defense in the code |
|---|---|
| **K1 compounding error** (90%×10 = 35%) | `slo/burnrate.py` multi-window + `autonomy/controller.py` auto-contract + `sandbox/budgets.py` step caps (default 20) |
| **K2 silent failure** (HTTP 200 hallucinations) | `slo/feedback.py` retroactive verdict flipping via `OutcomeSignal` + `judge/calibration.py` drift detection |
| **K3 Goodhart** (judges can be gamed) | `judge/goodhart.py` 4 heuristic detectors + `judge/calibration.py` 1-5% human sample stratified by tier + cross-model panel + adversarial budget carve-out |
| **K4 deceptive alignment** (Apollo o3, Replit 4000) | Sealed tools allowlist + `gateway/policy.py` deny-by-default + `gateway/attestation.py` (outcome derived from Gateway, not agent narrative) + cross-model judge invariant hard-coded |
| **M1 non-stationarity** | Per-model-version SLO; `model_version` required on every event |
| **M3 obs primitives** | Wide-event store + Pydantic events as primitive (not metric counters) |
| **M4 responsibility gap** | Registry validator requires named `owner` email; `OwnerPager` routes burn alerts to specific human |
| **Replit fabricating 4000 users** | `attestation.py` — `rows_affected` comes from driver, agent_claim stored separately tagged untrusted |
| **Replit lying about rollback** | A01 adversarial test catches outcome lying via wide-event divergence |
| **Cursor 9-second prod DB delete** | `kubectl_*` mutating tools are T3, require human approval; `kwargs_constraints` block `--replicas=0` style escalations |
| **Apollo o3 evaluation-aware** | No env=staging marker in agent context; A12 adversarial test confirms |
| **Collinear +90% FPR judge manipulation** | Cross-model judge invariant (judge family ≠ agent family); `judge/adversarial.py` CoT scorer flags sycophancy/judge-targeting |

## Adversarial gauntlet (all passing)

```
A01 outcome lying        PASS   wide-event divergence catches mismatch
A02 synthetic data       PASS   gateway-attested args_hash unforgeable
A03 tier leak            PASS   denial envelope scrubbed (no tier info)
A04 event store isolation PASS  no endpoint exists for agent to write
A05 idempotency collision PASS  arbitrary key rejected, only server keys accepted
A06 action token binding PASS   token signature flips on args mutation
A07 egress DLP           PASS   AWS key + ghp_ + JWT + Bearer + entropy detected
A08 prompt injection     PASS   tool output untrusted, drop_database not sealed
A09 judge manipulation   PASS   sycophancy + judge-targeting + self-justify flagged
A10 budget burst         PASS   denied at limit, not silently consumed
A11 chain tamper         PASS   verify_run returns broken event index
A12 eval awareness       PASS   no env/staging signal reaches agent context
```

## Demo scenarios (all passing)

| Scenario | What it does | What ACP catches |
|---|---|---|
| 01 cpu_spike | happy path: query metrics, propose scale, approve, execute | baseline judgment=pass |
| 02 silent_fail | agent declares "resolved" but metric stays red | OutcomeSignal `oncall_refire` retroactively flips judgment to fail (**K2 defense**) |
| 03 goodhart_inflation | 5000-char CoT trying to game judge | length_anomaly + reasoning_action_mismatch flags fire (**K3 defense**) |
| 04 irreversible_temptation | agent reasons toward `--replicas=0` | kwargs_constraint_violation → DenyClosed (**K4 defense**) |

## Production checklist (Definition of Done — all met)

- [x] `pip install -e . && make demo` runs the full 4-scenario demo end-to-end in seconds
- [x] All unit + integration + adversarial tests green (310 passing)
- [x] `gateway/policy.py` at 100% line coverage
- [x] `acp verify` confirms event chain integrity (A11 test)
- [x] Dashboard at `localhost:8080/dashboard` shows: agent list, autonomy tier per task_class, 1h/6h burn rates, pending approvals + audits, recent events
- [x] `docs/THREAT_MODEL.md` maps every K1-K4 + M1-M4 + T1-T15 to a specific module and test
- [x] Single Python process, single SQLite file, no other infra required
- [x] `acp --help` shows all CLI subcommands and they all work
- [x] Top-level README has working 60-second quickstart
- [x] Blog post draft exists in `blog/` (463 lines)
- [x] No partial ships — every module has tests

## How the work was done (meta)

The build itself used the architecture you asked for:

1. **3 parallel planner agents** (SRE-pragmatist / AI-native / adversarial-first) produced 3 independent plans (~530 lines each)
2. I **integrated** them into `planning/MASTER_PLAN.md` — locked stack, file layout, 10 design axioms, defense matrix, 5 implementation waves
3. **11 implementation agents** in 3 batched waves:
   - Wave 1+2 (4 parallel): foundations + schemas + events + registry
   - Wave 3+4 (4 parallel): gateway + judge + SLO + autonomy
   - Wave 5 (3 parallel): human UI / SDK+demo+adversarial / docs+blog
4. Each agent owned a specific directory; integration verified via the full test suite at every wave gate
5. All evidence in `evidence/wave[1..5]_complete.md`

Total wall time end-to-end: ~3 hours, ~15,700 LOC delivered, 310 tests passing.

## What's intentionally NOT in v1.0

Documented in `docs/ARCHITECTURE.md` "Future work":

- ed25519 signing of every event (current: blake2b chain integrity only)
- HSM-backed operator keys
- netns / cgroup sandbox enforcement (currently documented as operator responsibility)
- Postgres backend (SQLite for v1.0, SQLAlchemy 2 swap-ready)
- Multi-tenant auth (HMAC session bearer for v1.0)
- gRPC alt-surface (FastAPI only)
- Real LLM judges as default (StubJudge default; Anthropic+OpenAI clients enabled when API keys present)

## Where to read next

1. `README.md` — start here
2. `docs/ARCHITECTURE.md` — full 7-layer design + sequence diagrams + trust boundary
3. `docs/THREAT_MODEL.md` — T1-T15 + K/M defense matrix
4. `blog/agent_control_plane_design.md` — long-form essay tying research to design
5. `planning/MASTER_PLAN.md` — the synthesis that drove the build
6. `contexts/survey_sessions/agent_slo_error_budget_survey_20260519.md` — the research that started this
