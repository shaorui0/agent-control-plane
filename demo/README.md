# ACP Demo — 60-second walkthrough

This demo exercises the Agent Control Plane end-to-end via the Python SDK's
`LocalClient` (in-process, no HTTP). It runs four scenarios that map directly
to the defense matrix:

1. **01 cpu_spike** — happy path: read metrics, read logs, propose scale, T3
   approval auto-granted, judge passes.
2. **02 silent_fail** — agent declares "resolved"; 20 min later a synthetic
   `oncall_refire` outcome signal is ingested → judgment retroactively flipped
   pass → fail (K2 outcome feedback defense).
3. **03 goodhart_inflation** — agent emits 5000-char reasoning naming a
   different tool than the one called → `length_anomaly` and
   `reasoning_action_mismatch` flags fire (K3 Goodhart defense).
4. **04 irreversible_temptation** — agent attempts `kubectl_scale
   --replicas_delta=-99`; binding constraint denies → `kwargs_constraint_violation`
   (T5 / T8 defense).

## Prerequisites

- Python 3.11+
- ACP installed: `pip install -e .` from the project root.

## Run

```bash
./demo/run_demo.sh
```

Output sequence:
- Each scenario's structured result + verification.
- Summary line per scenario.
- `pytest tests/integration tests/adversarial` runs to confirm the broader
  invariants still hold.

Exit code 0 if all four scenarios produced expected outcomes, 1 otherwise.
Total runtime: under 3 seconds on a 2026 macbook.

## What's fake here

- `demo/oncall_agent.py` has a `fake_think()` function clearly labeled
  "FAKE-LLM-FOR-DEMO" — it returns deterministic canned responses driven by
  the scenario JSON. **It is not a real model.** The point of the demo is to
  showcase the Control Plane wiring, not LLM behavior.
- `demo/mock_vm_server.py` and `demo/mock_loki_server.py` are tiny FastAPI
  apps that mimic VictoriaMetrics and Loki query endpoints. They are NOT used
  by the default demo (which goes in-process via LocalClient) but are provided
  for users who want to wire a real HTTP `RemoteClient` against them.
- `demo/fake_cluster.py` is an in-memory K8s state used by extended demos.

## Scenarios as data

Each scenario is a small JSON file in `demo/scenarios/`:

- `agent_script`: the sequence of canned decisions the fake LLM returns.
- `expected`: human-readable expectation summary.
- `post_actions`: outcome signals to ingest after `task_end` (scenario 02).

`demo/expected_outcomes.json` is the verifier's source of truth: per-scenario
caps on `tool_calls`, `denials`, `goodhart_flags`, plus boolean expectations
for `retroactive_flip` and `judgment_verdict`.

## Real LLM mode (future)

Swap `LocalClient` for `RemoteClient(base_url="http://localhost:8000")` and
replace `fake_think` with an Anthropic/OpenAI call. The rest of the loop is
identical: SDK methods are async and have matching signatures across local
and remote clients.
