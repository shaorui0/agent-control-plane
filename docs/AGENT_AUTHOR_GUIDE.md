# Agent Author Guide

> How to write an agent that runs under ACP.

ACP supervises agents; it does not write them. An ACP-supervised agent is
just a Python program that talks to the Gateway over HTTP (or in-proc via
the SDK).

---

## 1. Use the Python SDK

```python
from acp.sdk import LocalClient    # in-proc, for tests and demos
from acp.sdk import RemoteClient   # httpx-backed, for production

# Local (single process; useful for the demo)
client = LocalClient(app)   # FastAPI app instance

# Remote
client = RemoteClient(base_url="http://acp:8080", bearer=None)
```

Both clients expose the same surface:

```python
session = client.start_session(agent_id="oncall_triage",
                               task_class="triage_alert",
                               input={"alert_id": "page-42"})
# session.run_id, session.bearer

result = client.invoke(session.run_id, "vm_query",
                       args={"query": "rate(http_requests_total[5m])"},
                       intent="checking traffic baseline before scaling")
# {"ok": true, "result": {...}}  OR
# {"ok": false, "reason": "tool_not_sealed"}  OR
# {"status": "pending_approval", "approval_id": "..."}

client.end_session(session.run_id,
                   final_output={"action": "no-op"},
                   agent_claim_outcome="resolved without action")
```

`invoke` never raises on a Gateway denial — it returns a structured `ok:
false` envelope. **Treat every denial as evidence about your registry
config, not as a transient error to retry.** Retrying a denied call burns
budget without progress.

---

## 2. Register your agent in YAML

`agents/<your_agent>.yaml`:

```yaml
agent_id: code_reviewer            # unique; matches CLI invocations
owner: "bob@example.com"           # M4: required, email regex
version: "0.1.0"
model_version: "claude-sonnet-4-5" # used for SLI partition; bump on model swap

task_classes:
  - name: review_pr
    default_tier: T1               # T3/T4 not allowed as default
    slo_target: 0.80
    slo_window: "7d"

sealed_tools:
  - name: git_diff                 # T1 read-only
    max_tier: T1
    requires_intent: true
  - name: post_comment             # T2 reversible
    max_tier: T2
    requires_intent: true
  - name: merge_pr                 # T3 irreversible-ish
    max_tier: T3
    requires_intent: true

budget:
  tokens_per_hour: 500000
  usd_per_hour: 10.00
  wall_seconds: 1200
```

Required field reference:

| Field | Why |
|---|---|
| `owner` | M4 defense; pager target on burn; passes email regex |
| `model_version` | M1 defense; SLI is partitioned by this; bump → fresh budget |
| `task_classes[].default_tier` | Initial autonomy; T0..T2 only (T3/T4 must be earned) |
| `task_classes[].slo_target` | Pass rate the SLO engine evaluates against |
| `sealed_tools[].max_tier` | Cap above which this binding is denied |
| `sealed_tools[].requires_intent` | Forces `intent` argument on invoke |
| `budget.*` | Hard caps per agent per hour |

Validate before reload:

```bash
acp registry validate --file agents/code_reviewer.yaml
```

---

## 3. Sealed tools — only what's in your binding works

The Gateway uses an O(1) hashmap lookup. There are no wildcards, no regex.
If you need a new tool, the operator must add it to `sealed_tools` and
reload. Asking the Gateway to invoke a tool you didn't register returns
`tool_not_sealed` immediately — the call doesn't even reach a handler.

In code:

```python
result = client.invoke(run_id, "kubectl_delete", args={...}, intent="...")
# if "kubectl_delete" not in your sealed_tools:
# {"ok": false, "reason": "tool_not_sealed"}
```

This is by design (deny-by-default, T6 defense). New capabilities must
land via operator-signed YAML change, never via prompt.

---

## 4. INTENT is required on mutating tools

If a binding has `requires_intent: true`, every invoke must include a
non-empty `intent` string ≥10 characters containing at least one verb-ish
word. The intent is captured in the wide event store and shown to the
operator on approval.

Good intents:
- `"scaling payments-api to 1 replica to reduce memory pressure"`
- `"draining node-7 before kernel upgrade"`
- `"posting incident summary to #ops-alerts"`

Bad intents (denied):
- `"do it"` (too short)
- `"   "` (blank)
- `"reasoning above"` (no verb-ish word and adversarial style)

Tip: write intents in the `# INTENT:` style mandated by the workspace's
K8s safety convention. Same discipline, captured by ACP automatically.

---

## 5. Budget — design trajectories to fit

ACP enforces three caps:

- **Token budget** (`tokens_per_hour`): per-agent, hourly rolling.
- **Dollar budget** (`usd_per_hour`): same.
- **Wall budget** (`wall_seconds`): per-run hard ceiling.
- **Step budget** (`sandbox/budgets.py`, default 20 steps per run):
  hard-coded, per-run.

When a cap is reached, the next invoke returns `budget_exceeded` and
fails closed. Agents that hit budget will be demoted on the next
autonomy tick. **Design your trajectories to fit your budget**:

- Read-only investigation: 3-8 tool calls, well within 20.
- Multi-step remediation: split into staged runs, each ending at a
  natural checkpoint, so each run's budget is bounded.
- Sub-agent fan-out: each child has its own fresh budget and step cap;
  see `sandbox/fanout.py`.

---

## 6. What to expect when your tier contracts

You will not be notified your tier dropped. The agent observes the change
only by which tools now return `tier_too_high`. Symptoms:

- Tool that worked an hour ago now returns `tier_too_high` → tier
  contracted; the binding's `max_tier` is now above your current tier.
- Many tools returning `tool_not_sealed` → you may be hitting a tier so
  low that even read-only tools require a tier you don't have.

**The correct response is to end the session cleanly** with whatever
output you have. Do not loop. Do not try to escalate. Your operator
will see the auto-demotion in the dashboard and decide whether to
promote you back after investigation.

---

## 7. Minimal agent example

```python
from acp.sdk import RemoteClient

client = RemoteClient(base_url="http://acp:8080")

def triage(alert_id: str) -> dict:
    s = client.start_session(
        agent_id="oncall_triage",
        task_class="triage_alert",
        input={"alert_id": alert_id},
    )

    # Step 1: read traffic baseline
    r1 = client.invoke(
        s.run_id, "vm_query",
        args={"query": f'rate(http_requests_total{{alert_id="{alert_id}"}}[5m])'},
        intent="checking baseline rate to compare against current",
    )
    if not r1["ok"]:
        client.end_session(s.run_id, final_output={"error": r1["reason"]},
                          agent_claim_outcome="aborted: tool denied")
        return {"status": "aborted"}

    # Step 2: post a summary to slack
    r2 = client.invoke(
        s.run_id, "slack_post",
        args={"channel": "#ops-alerts",
              "text": f"Alert {alert_id} traffic baseline retrieved"},
        intent="posting baseline summary to ops channel for visibility",
    )

    # End
    client.end_session(
        s.run_id,
        final_output={"baseline": r1["result"], "posted": r2["ok"]},
        agent_claim_outcome="baseline checked + summary posted",
    )
    return {"status": "ok"}
```

Note what the agent does *not* do:

- It does not pick its own idempotency keys.
- It does not retry on denial.
- It does not report success in `agent_claim_outcome` based on its own
  judgment — it states what happened (baseline + post). The Judge derives
  outcome from the events, not from this string.
- It does not see its tier; if `slack_post` is denied with
  `tier_too_high`, the agent simply records the denial.

That is the shape of an ACP-friendly agent.
