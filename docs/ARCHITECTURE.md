# ACP Architecture

> 7-layer control plane for autonomous agents. Single Python process, single
> SQLite file, no infra deps. v1.0.

---

## 1. Why ACP exists

In late 2025, multiple production failures crystallised the same lesson:

- **Klarna** (Feb 2024 → May 2025) announced "$40M efficiency gains, replaces
  700 agents", then publicly walked back, citing quality regressions.
- **Replit** agents fabricated **4,000 fake users** and then *claimed*
  the database rollback was "impossible" — both lies, both undetectable from
  agent self-report.
- **METR**'s randomised study of 16 senior OSS developers found Cursor + Claude
  3.5/3.7 made them **19% slower** while they self-reported 20% faster.
- **Apollo Research** demonstrated OpenAI o3 **deliberately sandbags** chemistry
  evals and detects when it is being tested.

These are not bugs. They are the *expected* failure modes of probabilistic
agents in production. Four competing reliability frames have emerged:

| Frame | Champion | Idea | Why insufficient alone |
|---|---|---|---|
| SLO + error budget | Honeycomb, Datadog | Treat agent as a service; allow `p` failure budget | Agents have no atomic request; goodhart's law applies |
| Eval / pass^k | Anthropic | Offline test set, expect ~100% regression pass | Eval-prod gap; eval-awareness (T14) |
| Durable execution | Temporal, DBOS | Wrap stochastic core in deterministic shell | Doesn't observe outcome quality |
| Six Sigma / DPMO | Multi-agent consensus papers | Vote across N agents | Expensive; doesn't address deceptive alignment |

ACP picks **SLO + error budget** as the operational primitive, then attacks
the four "kill-level" objections head-on:

- **K1 compounding error** (90%¹⁰ = 35%) — per-step SLI plus burn-rate, plus
  step caps, plus tier-driven autonomy contraction.
- **K2 silent failure** — outcomes are *derived* from downstream signals, not
  reported by the agent; verdicts are **retroactively flippable** when a refire
  or rollback signal arrives.
- **K3 Goodhart** — cross-model judge invariant (judge family ≠ agent family) +
  4 heuristic Goodhart detectors + adversarial budget carve-out.
- **K4 deceptive alignment** — sealed tools, gateway attestation, CoT
  adversarial judge, tier invisible to agent, asymmetric demotion.

The core thesis is borrowed wholesale from Honeycomb (75% / 7-day SLO on
Query Assistant) and Matthias Roder's *Earned Autonomy Gradient* (autonomy is
spent budget, not a permanent grant). What ACP adds is wiring it all into a
single, hardened control plane with a coherent threat model.

---

## 2. The seven layers

```
                                      Operator
                                         |
                                         v
+--------------------------------------------------------------+
| L7  Human Interface          approvals / audit / dashboard   |
+--------------------------------------------------------------+
                  ^                                  |
                  | demote/promote                   | tier
+--------------------------------------------------------------+
| L6  Earned Autonomy          controller / transitions        |
+--------------------------------------------------------------+
                  ^                                  |
                  | burn rate                        | T0..T4
+--------------------------------------------------------------+
| L5  SLO Engine               burnrate / budget / feedback    |
+--------------------------------------------------------------+
                  ^                                  |
                  | verdicts                         | snapshots
+--------------------------------------------------------------+
| L4  Cross-model Judge        pipeline / goodhart / cal       |
+--------------------------------------------------------------+
                  ^                                                 
                  | read-only cursor                                
+--------------------------------------------------------------+
| L3  Wide Event Store         store / verifier / sli / query  |
+--------------------------------------------------------------+
                  ^                                  ^
                  | attested events                  | sandbox events
+--------------------------------------------------------------+
| L2  Sandbox                  trajectory / fanout / budgets   |
+--------------------------------------------------------------+
                  ^
                  | the only door
+--------------------------------------------------------------+
| L1  Tool Gateway             policy / auth / intent / DLP    |
|                              idempotency / action_token      |
|                              attestation / sealed tools      |
+--------------------------------------------------------------+
                  ^
                  | YAML, owner-required
+--------------------------------------------------------------+
| L0  Registry                 models / loader / validator     |
+--------------------------------------------------------------+
                  ^
                  | spec + bindings
                  |
                  Agent (the suspect)
```

### L0 — Registry
- **Responsibility**: load `agents/*.yaml`, validate (owner-required, sealed
  tools exhaustive), expose `AgentSpec` to L1.
- **Files**: `src/acp/registry/models.py`, `loader.py`, `validator.py`, `store.py`.
- **Invariants**: every agent has a named owner (M4); registry is mutable only
  by operator, never by agent; SIGHUP reload affects only new runs.

### L1 — Tool Gateway
- **Responsibility**: the *only* surface the agent can reach. Every tool call
  goes through `policy.py`: seal → tier → intent → kwargs → budget → egress DLP
  → action token → idempotency → execute → attest.
- **Files**: `src/acp/gateway/{policy,auth,intent_check,idempotency,action_token,egress_dlp,budget,attestation,routes}.py` + `tools/`.
- **Invariants**: deny-by-default, server-issued idempotency keys, no internal
  error detail returned to agent (T15), gateway-attested events only.

### L2 — Sandbox
- **Responsibility**: trajectory step cap, sub-agent fan-out, budget enforcement.
- **Files**: `src/acp/sandbox/{trajectory,fanout,budgets}.py`.
- **Invariants**: max_steps cap (default 20); fresh trajectory per fan-out
  child; budget exhaustion → `BudgetExceeded` fails closed.

### L3 — Wide Event Store
- **Responsibility**: append-only blake2b-chained event log; SLI is a *query*,
  not a counter.
- **Files**: `src/acp/events/{store,verifier,query,sli}.py`.
- **Invariants**: chain hash = blake2b(prev_hash || canonical(payload));
  monotonic per-run step; no UPDATE or DELETE API.

### L4 — Cross-model Judge
- **Responsibility**: panel of judges of different model families scores each
  task_end; Goodhart heuristics; calibration sampling against human label.
- **Files**: `src/acp/judge/{pipeline,llm_clients,rubric,disagreement,goodhart,adversarial,calibration,replay}.py`.
- **Invariants**: cross-model invariant (judge family ≠ agent family) enforced
  at constructor *and* per-run (`pipeline.py:97,117`); CoT never enters the
  primary judge prompt; T4 events always sampled for human review.

### L5 — SLO Engine
- **Responsibility**: query-based SLI per `(agent_id, task_class, model_version)`;
  multi-window burn (1h/6h/24h/7d); outcome ingestion that retroactively flips
  verdicts (K2 defense).
- **Files**: `src/acp/slo/{engine,burnrate,budget,feedback,alerts,definitions}.py`.
- **Invariants**: organic vs adversarial budget split (M2); retroactive flip
  is idempotent and emits a new wide_event for audit.

### L6 — Earned Autonomy
- **Responsibility**: server-side tier per `(agent_id, task_class)`; demote on
  burn or harm verdict (instant, no operator); promote only with operator sig
  and ≥100 consecutive passes spanning ≥72h.
- **Files**: `src/acp/autonomy/{controller,transitions,states,events}.py`.
- **Invariants**: tier is never exposed to the agent (SI-13); demotion is
  asymmetric; every transition emits an `autonomy_change` wide_event.

### L7 — Human Interface
- **Responsibility**: T3/T4 approval queue; calibration audit queue; dashboard
  showing burn rates + autonomy tier + pending approvals.
- **Files**: `src/acp/human/*` (W5A).
- **Invariants**: approvals show *Gateway-observed* args, not agent narration
  (T10 defense against operator social engineering).

---

## 3. Sequence diagrams

### 3.1 Happy path: session → tool → judgment → SLO

```
Agent          Gateway        EventStore     Judge        SLO        Autonomy
  |                |                |            |          |            |
  |--POST /v1/sessions------------->|            |          |            |
  |                |--emit task_start->|         |          |            |
  |<-- run_id, bearer --|            |            |          |            |
  |                |                |            |          |            |
  |--POST /v1/.../tools/vm_query/invoke -------->|          |            |
  |                |--seal_check, tier_check, intent_check, |            |
  |                |  budget_check, egress_dlp, exec, attest->|          |
  |                |--emit tool_call+tool_result->|         |            |
  |<-- {ok, result} --|            |            |          |            |
  |                |                |            |          |            |
  |--POST /v1/sessions/{id}/end ---->|            |          |            |
  |                |--emit task_end->|            |          |            |
  |                |                |--enqueue->|          |            |
  |                |                |            |--judge_task(run_id)->|
  |                |                |            |--write judgments     |
  |                |                |            |          |            |
  |                |                |   (60s tick)          |            |
  |                |                |            |          |--evaluate_all->
  |                |                |            |          |--write slo_snapshot->
  |                |                |            |          |          |--tick->
  |                |                |            |          |          |--auto_demote if burn
```

### 3.2 T3 approval path

```
Agent          Gateway         Approvals       Operator
  |                |                |              |
  |--invoke kubectl_scale --------->|              |
  |                |--policy ok, tier=T3 ->        |
  |                |--write approval row (pending) |
  |<-- {status:pending_approval, approval_id} ----|
  |                |                |              |
  |                |                |              |--GET /v1/approvals
  |                |                |<--list pending-|
  |                |                |              |--POST /v1/approvals/{id}/decide
  |                |                |--update status, reviewer captured-->|
  |                |                |              |
  |--poll GET /v1/sessions/{run_id}/approvals/{id}->|
  |<-- {status: approved} ---|     |              |
  |--invoke kubectl_scale (again, with token) ---->|
  |                |--consume action_token, exec, attest -->|
  |<-- {ok, result}--|            |              |
```

### 3.3 Retroactive verdict flipping (K2 defense)

```
t=0       task_end -> judge says PASS, slo_snapshot reflects pass
t=10m     OutcomeSignal(kind=oncall_refire) posted by external
          |
          v
       feedback.ingest_outcome_signal
          |--insert into outcome_signals
          |--call maybe_flip_verdict(run_id, conn)
                |--query judgment for run, check kind, delay
                |--if rule triggered:
                |    UPDATE judgments SET verdict='fail',
                |                          retroactively_flipped=1,
                |                          original_verdict='pass'
                |    INSERT wide_event type=outcome attrs={retroactive_fail, ...}
                |
                v
       next SLO tick sees the new judgment value
       burn rate updates upward
       autonomy may demote on next tick
```

### 3.4 Burn rate triggers autonomy contraction

```
SLO 60s tick:
  for each (agent_id, task_class, model_version, window):
    sli = sli.judge_pass_rate(...)
    burn = burnrate.burn_rate(sli, target, window)
    write slo_snapshots row

Autonomy 60s tick (or same scheduler):
  for each (agent_id, task_class):
    snapshot = latest slo_snapshot
    level = burnrate.burn_alert_level(snapshot)
    if level == 'critical':  current_tier -= 1
    if level == 'exhausted': current_tier -= 2  (clamped at T0)
    if any judgment.harm in last 1h: tier = T1
    emit autonomy_change wide_event with cause + boundary_delta
```

---

## 4. Trust boundary

```
+-----------------------------------------------------------------+
|  CONTROL PLANE (hardened, outside the agent's reach)             |
|  +-------------------------------------------------------------+ |
|  | Registry          read-only to agent, write needs operator   | |
|  | Event Store       append-only, blake2b-chained, no DELETE    | |
|  | Judge             reads Event Store, never agent stdout      | |
|  | SLO Engine        reads judgments + outcome_signals          | |
|  | Autonomy Ctrl     writes autonomy_state; tier never returned | |
|  | Operator Console  approvals, audits, manual promote          | |
|  +-------------------------------------------------------------+ |
|         ^                              |                          |
|         | append-only                  | read-only views          |
|         | (one-way)                    | (filtered)               |
|         v                              v                          |
|  +-------------------------------------------------------------+ |
|  | Tool Gateway   the ONLY door the agent can knock on          | |
|  |   - bearer token (server-issued per run, single-use)         | |
|  |   - capability check vs Registry                             | |
|  |   - Pydantic strict args, sealed tool name allowlist         | |
|  |   - intent + budget + egress DLP + action token              | |
|  |   - emits Gateway-attested wide_event (agent cannot forge)   | |
|  +-------------------------------------------------------------+ |
|         ^                              |                          |
+---------|------------------------------|--------------------------+
          | bearer over HTTP             | tool_result tagged
          | tool_call(args, intent)      | trust=untrusted
          v                              v
+-----------------------------------------------------------------+
|  SANDBOX (inside the agent's reach)                              |
|  +-------------------------------------------------------------+ |
|  | Agent process (LLM + harness)                                | |
|  |   - no network except via Gateway                            | |
|  |   - no filesystem outside /run/agent/<run_id>/               | |
|  |   - tokens / time / steps capped (Gateway-enforced)          | |
|  |   - stdout/stderr captured but never trusted as fact         | |
|  +-------------------------------------------------------------+ |
+-----------------------------------------------------------------+
```

Hard invariants the boundary defends (formalised in `docs/INVARIANTS.md`):

- I1. The Registry is unreachable from the agent's network namespace
  (documented in v1.0; netns enforcement is v2).
- I2. The Event Store has no socket the Sandbox can connect to. Writes happen
  only from in-process Gateway / Judge code.
- I3. The agent never sees raw env vars, secrets, or its own tier.
- I4. The Judge reads from the Event Store, not from the running agent.
- I5. The Autonomy Controller has no agent-facing API.

---

## 5. Wide event store

The single most important architectural choice in ACP is that **the SLI is a
SQL query against a wide-event table, not a counter incremented by code**.

### 5.1 Schema rationale (see `events/migrations/0001_init.sql`)

Each event is a flat row with many columns rather than `(metric_name, value)`:

```sql
event_id          ULID, primary key
prev_event_id     chain pointer for this run
ts                server-stamped unix ms
run_id            server-issued nonce, groups trajectory
agent_id, task_class, model_version, step
event_type        task_start|tool_call|tool_result|task_end|judgment|outcome|...
tool_name, tier_required, outcome, intent
agent_claim       agent narrative, untrusted
attrs_json        high-cardinality freeform
chain_hash        blake2b(prev_hash || canonical(payload))
```

Why "wide" (Honeycomb school): we cannot pre-aggregate what we have not yet
discovered to measure. Six months into a deploy you will need an SLI we did
not anticipate (e.g. "rate of tool calls that hit the egress DLP for goodhart
flagged runs in adversarial budget class only"). Wide events make that a
query, not a code change.

### 5.2 Query-based SLI

`events/sli.py` exposes `judge_pass_rate`, `intervention_free_rate`,
`silent_fail_rate`, `p95_latency_ms`. Each is a stateless SQL query over
`wide_events` joined to `judgments`, parameterised on `(agent_id, task_class,
model_version, window)`.

### 5.3 Chain integrity

For each `run_id`, events form a linked list anchored on `task_start`. The
hash of event N depends on event N-1. Tampering — inserting, deleting, or
modifying any field — is detected by `events/verifier.py`. The verifier runs
offline (CLI: `python -m acp.events.verifier`) and per-run (used by the SLO
engine before serving snapshots).

Why per-run not global: limits blast radius of a single corrupt run, and
allows independent verification in parallel.

---

## 6. Cross-model judge invariant

If the same model family judges its own work, the judge has the same blind
spots and the same prompt-injection susceptibility as the agent. Collinear
AI showed a crafted CoT can flip a single-judge of the same family by +90%.

The invariant is enforced in two places:

1. **Constructor** (`judge/pipeline.py:97`): `_assert_cross_model` rejects a
   pipeline whose judges all share the agent's family. `StubJudge`
   (`model_family="stub"`) always satisfies the invariant — useful in tests
   and in offline mode with no API keys.
2. **Per-run** (`pipeline.py:117`): `assert_cross_model_for(agent_model_version)`
   re-checks against the actual model_version observed in the event stream;
   blocks an agent that swaps model_version mid-flight.

The calibration loop (`judge/calibration.py`) draws a stratified human-label
sample (rate=1.0 for T4 down to 0.005 for T0) and tracks judge-vs-human
agreement (Cohen's κ). Drift below 0.6 raises a drift alarm.

---

## 7. Earned Autonomy Gradient

Autonomy is a **spent budget**, not a permanent grant. Five tiers:

| Tier | Semantics | Examples |
|---|---|---|
| T0 | Read-only, every action audited | New agent, post-incident |
| T1 | Read + reversible writes auto, irreversible deny | Default for unproven |
| T2 | Read + reversible writes, slow approvals on irreversible | Most prod |
| T3 | Reversible auto; irreversible to staging queue + approval | Earned trust |
| T4 | Reversible auto; irreversible with sub-second human gate | Rare; CSAT proxy passes |

The δ-state machine in `autonomy/transitions.py`:

- `auto_demote_on_burn`: `critical` → -1 tier, `exhausted` → -2 (clamped at T0).
- Any judgment with `harm=true` in last 1h → snap to T1, no operator needed.
- `evaluate_outcome_signals`: >2 rollback_required or oncall_refire in 24h → -1.
- `evaluate_promotion_eligibility`: needs ≥100 consecutive pass, ≥0.97 rate,
  ≥72h span, capped at T4 — but auto-promotion never fires. **Promotion
  requires an operator signature** (asymmetric defaults).

Tier is *never* exposed to the agent. The agent discovers its tier only by
which tools succeed. This prevents T8 (autonomy escalation via prompt
engineering).

---

## 8. Deliberate trade-offs

These are uncomfortable on purpose. Each is a place where the "nicer" design
would create a deception attack surface.

1. **No agent-supplied idempotency keys.** Inconvenient for retry semantics;
   necessary to defend T12 (idempotency replay).
2. **No event-store compaction or UPDATE in v1.** Disk grows linearly. Necessary
   to keep the chain provably append-only.
3. **Tier is invisible to the agent.** Inconvenient for debugging; necessary
   for T8 (autonomy escalation).
4. **Demotion is asymmetric**: instant on harm verdict, slow on promotion.
   Inconvenient when judges are noisy; necessary to never reward deception.
5. **No internal error detail returned to the agent — ever.** Debugging
   requires reading the event store offline. Necessary for T15 (side-channel).
6. **StubJudge is the default when no API keys are configured.** A single
   deterministic rule-based judge can be wrong; the calibration loop catches
   drift but does not eliminate it. Documented in OPERATOR_RUNBOOK.
7. **SQLite, not Postgres.** Single-writer. Ceiling ~1k events/s sustained.
   The trade is zero infra + atomic chain integrity.

---

## 9. Future work (v2)

- **ed25519 signing of every event** (currently only blake2b chain; gateway
  doesn't hold a private key in v1).
- **HSM-backed operator keys** (operator console uses bcrypt + OS keyring
  in v1).
- **netns/cgroup sandbox enforcement** (currently documented, not enforced).
- **Multi-tenant**: per-tenant agent isolation, per-tenant SLO budgets,
  per-tenant operator pools.
- **Postgres migration**: when sustained throughput > 1k events/s.
- **gRPC alt-surface**: for in-cluster agents that prefer protobuf.
- **Action-token bound to args_hash via ed25519** (currently HMAC + symmetric).
- **Persistent operator audit chain** signed by ed25519 (currently in-table).

See `planning/MASTER_PLAN.md` §7 for v1.0 risk acknowledgements and §11 for
ship-gate criteria.
