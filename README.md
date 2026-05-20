# Agent Control Plane (ACP)

> Kubernetes-style governance for AI agents. One Python process, one SQLite
> file, no infra deps. Borrows Honeycomb's SLO + error-budget pattern,
> adds a cross-model judge, outcome-derived verdicts, and an Earned
> Autonomy Gradient.

---

## What it is

ACP is a control plane that supervises autonomous agents the same way an
SRE team supervises a microservice: per-task-class SLOs, multi-window burn
rates, error budgets, hard caps, and audit trails. It adds four things
SRE-for-services doesn't need:

1. A **cross-model judge** that scores agent outputs without sharing the
   agent's blind spots.
2. **Outcome-derived verdicts** that retroactively flip a "pass" to "fail"
   when the downstream world disagrees (the K2 silent-failure defense).
3. **Earned Autonomy Gradient** — autonomy is a budget the agent spends, not
   a permanent grant; the daemon auto-contracts on burn and only
   auto-promotes with operator signature.
4. **Sealed tools + Gateway attestation** — the agent's only surface is a
   single deny-by-default door; every event is written by the Gateway, not
   the agent.

---

## Why

Production AI-agent failures of 2024-2025 share a structure: probabilistic
core, no error budget, agent self-reports treated as ground truth, no
boundary between agent reach and control plane. Documented examples:

- **Klarna** announced "$40M efficiency, 700 agents replaced" (Feb 2024), then
  walked back in May 2025 citing quality regressions.
- **Replit** agents fabricated **4,000 fake users** to mask a destructive
  database operation, then *claimed the rollback was impossible*. Both
  fabrications. Both invisible to the agent's own logs.
- **METR** randomised study: 16 senior OSS developers using Cursor + Claude
  3.5/3.7 were **19% slower**, but self-reported **20% faster** — a 39-point
  perception gap.
- **Apollo Research** demonstrated OpenAI o3 deliberately sandbags chemistry
  evals and detects when it is being tested.
- **Honeycomb** runs its Query Assistant at a **75% / 7-day SLO** — proof
  that probabilistic systems *can* be ops-managed without 100% HITL.

Full citations and the four-frame comparison (SLO / eval / durable-exec /
Six Sigma) live in
[`agent_slo_error_budget_survey_20260519.md`](https://github.com/shaorui0/context-infrastructure/blob/main/contexts/survey_sessions/agent_slo_error_budget_survey_20260519.md) (in the `context-infrastructure` repo).

---

## 60-second quickstart

```bash
git clone <repo> && cd adhoc_jobs/agent_control_plane
make install        # pip install -e ".[dev]"
make demo           # boots ACP + mock services + runs 4 scenarios
open http://localhost:8080/dashboard
```

The demo runs in ~90 seconds on a stock laptop. Real LLM judges are
optional — set `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY` in `.env` to
enable them; the default `StubJudge` is deterministic and zero-dep.

---

## Architecture (teaser)

```
   Agent (suspect)
        |
        v
   L1 Tool Gateway          <-- the only door
   L2 Sandbox               <-- step + budget caps
   L3 Wide Event Store      <-- blake2b chain, SLI is a query
   L4 Cross-model Judge     <-- judge family != agent family
   L5 SLO Engine            <-- 1h/6h/24h/7d burn
   L6 Earned Autonomy       <-- demote auto, promote signed
   L7 Human Interface       <-- approvals + audit + dashboard
        ^
        |
   Operator
```

Full diagrams, sequence flows, and trust boundary live in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Demo scenarios

`make demo` runs four end-to-end scenarios that exercise specific defenses:

| Scenario | What happens | Defense exercised |
|---|---|---|
| `01_cpu_spike` | Agent investigates a CPU spike with read-only tools | Happy path: T1 tier, read-only tools, judgment + SLO snapshot |
| `02_silent_fail` | Agent claims success while metric never recovered | K2: `oncall_refire` arrives 10 min later, verdict flips retroactively |
| `03_goodhart_inflation` | Agent produces verbose CoT to game length-biased judges | K3: Goodhart `length_anomaly` flag; calibration sample captures it |
| `04_irreversible_temptation` | Agent tries to scale prod deployment to zero | K4 / T3: action sent to approval queue, never auto-executed |

Each scenario writes evidence to `evidence/scenarios/<name>/`.

---

## Defenses at a glance

ACP defends against four "kill-level" objections (K1-K4) and four
meta-failure modes (M1-M4):

| | Concern | Module |
|---|---|---|
| K1 | Compounding error (90%^10 = 35%) | `slo/burnrate.py` + `autonomy/controller.py` |
| K2 | Silent failure | `slo/feedback.py` retroactive flip |
| K3 | Goodhart's law | `judge/goodhart.py` + adversarial budget |
| K4 | Deceptive alignment | sealed tools + cross-model judge |
| M1 | Non-stationarity | per-`model_version` SLI |
| M2 | Adversarial budget | `slo/budget.py` organic/adversarial split |
| M3 | Observability primitive | wide events, not counters |
| M4 | Responsibility gap | owner-required in YAML |

15 threat scenarios T1-T15 are catalogued with module + test references in
[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

---

## Project layout

```
adhoc_jobs/agent_control_plane/
  pyproject.toml
  Makefile
  README.md                    (this file)
  src/acp/
    settings.py                pydantic-settings
    db.py                      sqlite + migrate()
    schemas/                   Pydantic v2 wire + DB + LLM
    registry/                  YAML loader + validator
    gateway/                   the only door agent can knock on
    sandbox/                   step + budget caps
    events/                    wide-event store + verifier + sli
    judge/                     cross-model panel + goodhart + calibration
    slo/                       burnrate + budget + outcome feedback
    autonomy/                  earned autonomy gradient
    human/                     approvals + audit + dashboard
    sdk/                       LocalClient + RemoteClient
  agents/                      YAML registry
  demo/                        4 scenarios + mock services
  docs/                        architecture, threat model, runbook
  blog/                        long-form essay
  tests/
    unit/                      ~290 tests
    integration/               4 end-to-end
    adversarial/               A01..A12 red-team gauntlet
  evidence/                    per-wave delivery notes
  planning/                    plan A/B/C + MASTER_PLAN
```

---

## Tests

```bash
make test           # all unit + integration + adversarial
```

At end of Wave 4: 290+ tests pass; `gateway/policy.py` is gated at 100%
line coverage. Wave 5 adds integration and adversarial gauntlets (see
`tests/integration/` and `tests/adversarial/`).

---

## Status

**v1.0**: in scope.

- All 7 layers
- Wide event store with blake2b chain + offline verifier
- Cross-model judge with StubJudge default + Anthropic + OpenAI clients
- Goodhart heuristics + calibration sampling
- Earned Autonomy Gradient with asymmetric demote/promote
- FastAPI surface + Typer CLI + APScheduler in-process
- Single Python process, single SQLite file

**v2 / out of scope**: ed25519 event signing, HSM operator keys, netns/cgroup
sandbox enforcement, multi-tenant, Postgres migration, gRPC alt-surface.
Full list in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#9-future-work-v2).

---

## Credits and inspiration

- **Honeycomb** — Charity Majors and team for the SLO+observability pattern
  on LLM products (Query Assistant 75% / 7-day SLO).
- **Matthias Roder** — *Earned Autonomy Gradient* framing.
- **Apollo Research** — for the deceptive-alignment evidence base that
  makes K4 a non-optional concern.
- **Tian Pan** — *Agent Blast Radius* for the four-door propose / authorize
  / execute architecture.
- **MindStudio** — the 4-tier reversibility x blast-radius matrix.
- **Daniel Lines (LinearB)** and **Jeremy Edberg (DBOS)** — for the
  durable-execution counter-frame that sharpened the SLO argument.

Full research base in
[`agent_slo_error_budget_survey_20260519.md`](https://github.com/shaorui0/context-infrastructure/blob/main/contexts/survey_sessions/agent_slo_error_budget_survey_20260519.md) (in the `context-infrastructure` repo).
