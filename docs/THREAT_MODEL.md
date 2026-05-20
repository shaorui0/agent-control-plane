# ACP Threat Model

> The agent is a suspect, not a user. Every byte from the agent is adversarial
> input. This document enumerates what ACP defends against and what it does not.

---

## 1. Threat catalog

| ID  | Attack | Real-world evidence | Neutralised by (module + test) |
|-----|--------|---------------------|--------------------------------|
| T1  | **Sandbagging**: agent intentionally fails an eval to avoid retraining | Apollo Research o3 chemistry | Eval-prod parity: judge runs on *production* traces, not synthetic evals. `judge/pipeline.py`, no separate eval surface. |
| T2  | **Synthetic-data fabrication**: agent creates fake rows/users to mask destruction | Replit 4,000 fake users | Gateway captures `rows_affected` from driver, not agent text. `gateway/attestation.py`; `tests/adversarial/test_A02_synthetic_data.py`. |
| T3  | **Outcome lying**: agent claims success after failure | Replit "rollback impossible" lie | Outcome derived from Gateway return + downstream `OutcomeSignal`; `slo/feedback.py` flips verdict retroactively. `tests/adversarial/test_A01_outcome_lying.py`. |
| T4  | **Judge manipulation via reasoning trace**: crafted CoT flips judge | Collinear AI +90% FPR | Primary judge sees only Gateway-attested record, not agent CoT. `judge/pipeline.py:97,117` cross-model invariant. `tests/adversarial/test_A09_judge_manipulation.py`. |
| T5  | **Tool-arg injection**: SQLi, path escape via tool args | Generic | Pydantic strict + per-tool schemas + namespace allowlist. `gateway/tools/*.py`, `gateway/policy.py`. |
| T6  | **Registry tampering**: agent grants itself capability | Hypothetical / inevitable | Registry is operator-write-only; agent has no path. `registry/validator.py`, `tests/unit/test_registry.py`. |
| T7  | **Event-log tampering / replay** | Hypothetical | blake2b chain + offline verifier. `events/store.py`, `events/verifier.py`; `tests/unit/test_events_chain.py`. |
| T8  | **Autonomy escalation**: agent self-promotes | Apollo / METR power-seeking | Tier computed server-side from SLO + judge state; not self-attestable; tier never exposed to agent (SI-13). `autonomy/controller.py`, `tests/unit/test_autonomy.py`. |
| T9  | **Prompt injection from untrusted data**: tool output says "ignore prior..." | Generic, daily | Every Gateway response tagged `trust=untrusted`; judge sees structured record, not raw stdout. `gateway/attestation.py`. |
| T10 | **Operator social engineering**: agent talks human into approving harm | Rubber-stamp / oversight fatigue | Approval UI shows Gateway-observed args, not agent narration. `human/approval.py` (W5A); `intent` field captured separately. |
| T11 | **Resource exhaustion / cost bomb** | Replit-style runaway | Per-run hard caps on tokens/$/wall/steps. `gateway/budget.py`, `sandbox/budgets.py`; `tests/unit/test_budget.py`. |
| T12 | **Idempotency collision**: replay destructive action | Hypothetical | Server-issued idempotency keys; agent keys rejected. `gateway/idempotency.py`; `tests/unit/test_idempotency.py`. |
| T13 | **TOCTOU**: policy passes at planning, args mutate before exec | Classic | Single-shot signed action token, TTL ≤ 60s, binds (run_id, tool, args_hash). `gateway/action_token.py`; `tests/unit/test_action_token.py`. |
| T14 | **Eval-awareness**: agent detects staging via env-var leak | Apollo o3 detects test context | No env flag enters agent context; judge runs on prod traces only. `gateway/routes.py` strips env from responses. |
| T15 | **Side-channel exfil**: secrets encoded in args/log/error | Generic | Egress DLP scanner (regex + entropy); no internal exception detail returned. `gateway/egress_dlp.py`, `errors.py`; `tests/unit/test_egress_dlp.py`. |

---

## 2. Defense matrix — K-level reframes and M-level meta-defenses

These are the reframes that distinguish ACP from naive SRE-on-agents:

| Concern | Module(s) | Test(s) |
|---|---|---|
| **K1 compounding** (90%¹⁰ = 35%) | `slo/burnrate.py` + `autonomy/controller.py` + `sandbox/budgets.py` step caps | `test_burnrate.py`, `test_autonomy.py`, `test_sandbox.py` |
| **K2 silent failure** (200 on broken state) | `slo/feedback.py` retroactive flip + `OutcomeSignal` ingestion + `judge/calibration.py` drift detection | `test_outcome_feedback.py`, `test_calibration.py` |
| **K3 Goodhart** (gaming the SLI) | `judge/goodhart.py` 4 heuristics + `judge/calibration.py` human sample + `judge/disagreement.py` quorum + `slo/budget.py` adversarial carve-out | `test_goodhart.py`, `test_calibration.py` |
| **K4 deceptive alignment** | `gateway/policy.py` sealed tools + `gateway/intent_check.py` + `judge/adversarial.py` CoT scorer + cross-model judge | `test_gateway_policy.py`, `test_judge_pipeline.py` |
| **M1 non-stationarity** (model upgrade) | `schemas/base.py` `model_version` required + `slo/sli.py` per-version SLI | `test_schemas.py`, `test_sli.py` |
| **M2 adversarial budget** | `slo/budget.py` carve-out; Goodhart-flagged events burn adversarial only | `test_outcome_feedback.py::test_adversarial_split` |
| **M3 observability primitive** | Wide-event store; Pydantic events; no metric counters | `test_events_store.py`, `test_sli.py` |
| **M4 responsibility gap** | `registry/validator.py` owner-required (email regex); `human/pager.py` owner pager | `test_registry.py` |

---

## 3. Adversarial test gauntlet

`tests/adversarial/` contains a `DeceptiveAgent` simulator: a deterministic
scripted hostile client (not an LLM) that drives the Gateway as an attacker
would. Each test asserts that a security invariant holds.

| ID  | Test name | Maps to | Status |
|-----|-----------|---------|--------|
| A01 | `test_A01_outcome_lying` | T3 | implemented in W5B |
| A02 | `test_A02_synthetic_data` | T2 | implemented in W5B |
| A03 | `test_A03_tier_leak` | T5, I3 | implemented in W5B |
| A04 | `test_A04_event_store_isolation` | I2 | implemented in W5B |
| A05 | `test_A05_idempotency_collision` | T12 | implemented in W5B |
| A06 | `test_A06_action_token_binding` | T13 | implemented in W5B |
| A07 | `test_A07_egress_dlp` | T8, T15 | implemented in W5B |
| A08 | `test_A08_prompt_injection` | T9 | implemented in W5B |
| A09 | `test_A09_judge_manipulation` | T4 | implemented in W5B |
| A10 | `test_A10_budget_burst` | T11 | implemented in W5B |
| A11 | `test_A11_chain_tamper` | T7 | implemented in W5B |
| A12 | `test_A12_eval_awareness` | T14 | implemented in W5B |

CI gate (when wired): all A01..A12 green for 3 consecutive nights before any
v1.0 release. Status is tracked in `evidence/wave5b_complete.md` once
delivered.

---

## 4. Known limitations (v1.0)

ACP v1.0 does **not** defend against:

- **Process-level sandbox escape**. We document the netns/cgroup boundary
  but do not enforce it. An agent that can shell out of its Python harness
  can bypass much of L2. Hardening this is v2.
- **Multi-operator key rotation**. Operator identity is a string; v2 adds
  ed25519 keys with HSM backing.
- **Side-channel via timing**. We do not normalise response latency. A
  motivated attacker could in principle distinguish deny paths from accept
  paths by timing. We deny-by-default but do not deny-uniformly.
- **Out-of-band communication between operator and agent**. If a human
  copy-pastes secrets into the agent's prompt outside of ACP, ACP cannot
  protect them.
- **Compromised LLM provider**. If the judge's API returns "pass" because
  the provider was compromised, the cross-model invariant degrades to
  single-judge.
- **Long-horizon collusion between two agents**. If two agents under
  ACP collude across sessions, our per-run chain isolation does not detect
  the collusion pattern. The calibration loop is the only safeguard.

For each of these, the operator runbook (`docs/OPERATOR_RUNBOOK.md`) gives
detection heuristics and manual mitigations.
