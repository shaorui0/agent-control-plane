# ACP Operator Runbook

> How to run ACP in anger. Read end-to-end once; refer back per situation.

---

## 1. Deploy

ACP v1.0 is a single Python process. No Redis, no Kafka, no Postgres.

```bash
# Clone and install
git clone <repo>
cd adhoc_jobs/agent_control_plane
pip install -e ".[dev]"

# Optional: enable real LLM judges
cp .env.example .env
# edit .env, set ANTHROPIC_API_KEY=... and OPENAI_API_KEY=...
# both optional; absence → StubJudge (deterministic rule-based)

# Boot
acp serve --port 8080 --db ./acp.db --registry ./agents/
```

The server runs:
- FastAPI on `:8080` (`/v1/*` agent surface + operator endpoints + `/dashboard`)
- APScheduler in-process: SLO eval every 60s, autonomy tick every 60s,
  judge worker pulling unjudged runs every 5s
- SQLite WAL at `--db` path (auto-migrated on first start)

Required env / settings (see `src/acp/settings.py`):

| Variable | Default | Purpose |
|---|---|---|
| `ACP_DB_PATH` | `./acp.db` | SQLite file |
| `ACP_REGISTRY_DIR` | `./agents/` | YAML registry root |
| `ACP_PORT` | `8080` | Bind port |
| `ANTHROPIC_API_KEY` | — | enables `AnthropicJudge` |
| `OPENAI_API_KEY` | — | enables `OpenAIJudge` |
| `ACP_ACTION_TOKEN_SECRET` | (random per boot) | HMAC for action tokens |

Health: `GET /healthz` (process up) and `GET /readyz` (db migrated, schedulers
running).

---

## 2. Onboard a new agent

1. Author YAML in `agents/<agent_name>.yaml`. Required fields (see
   `registry/validator.py`):

   ```yaml
   agent_id: oncall_triage
   owner: "alice@example.com"          # M4: named owner; regex-validated
   version: "0.1.0"
   model_version: "claude-sonnet-4-5"  # used for per-version SLI (M1)
   task_classes:
     - name: triage_alert
       default_tier: T1                # T3/T4 must be earned, never default
       slo_target: 0.75
       slo_window: "7d"
   sealed_tools:
     - name: vm_query
       max_tier: T1
       requires_intent: true
     - name: kubectl_get
       max_tier: T1
       requires_intent: true
   budget:
     tokens_per_hour: 200000
     usd_per_hour: 5.00
     wall_seconds: 600
   ```

2. Run the validator standalone (catches errors before hot-reload):

   ```bash
   acp registry validate --file agents/oncall_triage.yaml
   ```

3. Register / hot-reload:

   ```bash
   acp registry reload          # rescans agents/ dir
   # or: kill -HUP $(pgrep -f 'acp serve')
   # or: curl -X POST http://localhost:8080/v1/registry/reload
   ```

4. Verify:

   ```bash
   curl http://localhost:8080/v1/agents | jq '.[] | select(.agent_id=="oncall_triage")'
   ```

   Should show the agent + current tier per task_class.

---

## 3. Read the dashboard

Open `http://localhost:8080/dashboard`. Three sections:

### Agent table
- One row per `(agent_id, task_class)`.
- **Tier**: current T0..T4. Click to see transition history.
- **1h / 6h / 24h / 7d burn rate**: ratio of *current* error rate to the rate
  that would consume the SLO budget over the window. > 1.0 = burning faster
  than budget allows.
- **Budget remaining**: fraction of the SLO window's error budget left.
- **Status**: green / yellow / red derived from `slo/burnrate.py::burn_alert_level`.

### Pending approvals
- T3/T4 actions awaiting human decision.
- Shows: agent, tool, intent (agent-supplied, treat as untrusted), Gateway-
  observed args (trustworthy), pre-state snapshot.

### Audit queue
- Sample drawn by `judge/calibration.py` (rate per tier).
- Shows: trace, judge panel verdict, your job is to add a `human_label`.

### Burn rate interpretation cheat-sheet

| burn_rate | window | level | typical operator action |
|---|---|---|---|
| < 0.5 | 24h | ok | watch |
| 0.5 – 1.0 | 24h | ok | watch |
| 1.0 – 5.0 | 6h | warn | check trace, look for goodhart flag |
| > 5.0 | 1h | critical | autonomy auto-demoted; investigate |
| > 1.0 | 7d | exhausted | budget already spent; demote -2 |

---

## 4. Respond to a burn alert

When `burnrate.burn_alert_level` flips to `critical` or `exhausted`:

1. **Identify**: dashboard shows which `(agent_id, task_class, model_version)`
   tripped. Note the window.

2. **Pull the trace** of the most recent failed run:

   ```bash
   acp trace --run-id <id>
   # or: curl http://localhost:8080/v1/trace/<run_id>
   ```

3. **Look for goodhart flags** on the failed events:

   ```sql
   SELECT signal, evidence_json FROM goodhart_flags
   WHERE event_id IN (SELECT event_id FROM wide_events WHERE run_id = ?);
   ```

   Signals to recognise:
   - `length_anomaly` — verbose CoT to game length-biased judges
   - `reasoning_action_mismatch` — narrative diverges from tool calls
   - `self_citation` — agent quotes its own prior reasoning as evidence
   - `metric_local` — outcomes overfit the SLI metric

4. **Decide regression vs adversarial input**:
   - If `goodhart_flag` present + `agent_claim != Gateway-observed outcome` →
     real K3 regression. Demote the agent, file a model_version bump.
   - If many failures correlate with a specific input pattern → adversarial
     input. Move those events into the adversarial budget (`slo/budget.py`
     does this automatically when `goodhart_flag` is present).
   - If model_version changed in last 24h → M1 non-stationarity. Roll back
     the model_version pin.

5. **Manual demote if needed** (auto-demote should have already fired):

   ```bash
   acp autonomy demote --agent oncall_triage --task-class triage_alert \
                       --to T0 --reason "burn investigation, model regression suspected" \
                       --operator alice@example.com
   ```

6. **Document**: write incident notes in the autonomy_state.last_reason via
   the CLI; the change is captured as a wide_event for replay.

---

## 5. Chain break

The event chain is verified offline by `acp verify`. If a break is detected:

```bash
acp verify --db ./acp.db
# Output: chain break in run_id <id> at event_id <eid>
```

Runbook:

1. **Stop the SLO engine** from serving snapshots that include this run:

   ```bash
   acp slo freeze --run-id <id>
   ```

   (Implemented as a flag in `slo_snapshots`; engine skips frozen runs.)

2. **Investigate**: compare `prev_event_id` and `chain_hash` of adjacent
   events. A break usually means:
   - **Disk corruption**: check `dmesg`, run `PRAGMA integrity_check;`
     against the SQLite file. Recovery: restore from WAL backup.
   - **Tampering**: extremely unlikely in single-process v1.0 (no agent
     write surface) but possible if operator restored from a corrupt
     backup. Forensic: diff `wide_events` rows against any backup.
   - **Bug**: if `_chain_payload` was changed without a migration, all new
     events post-change will fail. Fix: roll back the code, do not
     "repair" the chain.

3. **Do not** repair the chain manually. The whole point of the chain is
   that it cannot be silently rewritten.

4. **Restart** with the affected run frozen. Add a postmortem note in
   `docs/INCIDENTS.md` (operator-maintained).

---

## 6. Approval workflow

When an agent invokes a T3+ tool, the Gateway writes an `approvals` row with
status `pending` and returns `{status: "pending_approval", approval_id}` to
the agent. The agent polls.

Operator side:

```bash
# List pending
acp approvals list --status pending
# Decide
acp approvals decide --id <approval_id> --decision approved \
                     --reviewer alice@example.com \
                     --notes "verified pre-state matches, blast radius confirmed limited"
```

The CLI captures `reviewer` (your identity), `notes`, and writes the
decision atomically. The agent's next poll receives `approved` and may
re-invoke with the action token issued at decision time.

Rejection: same flow, `--decision rejected`. The agent receives `rejected`
and must end the run.

**Do not approve based on agent narration.** The dashboard shows
*Gateway-observed* args (the actual tool input). The `intent` field is
agent-supplied and tagged untrusted; use it only as a hint about *why*,
never about *what*.

---

## 7. Calibration audit

`judge/calibration.py` draws a stratified sample from each tier (rate=1.0
for T4 down to 0.005 for T0) and pushes them onto the `audit_queue` table
with `reason="sample"`.

Workflow:

```bash
acp audit list --status pending
acp audit decide --id <audit_id> --human-label pass --notes "verified outcome"
```

Allowed `human_label` values: `pass`, `fail`, `uncertain`, `escalate`.

The `calibration` table accumulates `(judge_panel_label, human_label,
delta)`. The drift alarm fires when judge-vs-human Cohen's κ < 0.6 over the
last 100 samples. On drift:

1. **Do not auto-demote** all agents — the *judge* is the suspect.
2. Inspect which judge model is drifting (`judge_model` column in
   `calibration`).
3. If a specific judge is off, disable it (`--disable-judge` flag) and
   resample.
4. If all judges agree but diverge from human, you have a model upgrade or
   prompt drift — investigate before re-enabling.

---

## 8. Promote an agent

Eligibility is automatic; *application* is manual.

```bash
acp autonomy eligibility --agent oncall_triage --task-class triage_alert
# returns: eligible=true, consecutive=124, rate=0.98, span_hours=96, target=T2

acp autonomy promote --agent oncall_triage --task-class triage_alert \
                     --to T2 --operator alice@example.com \
                     --reason "100+ clean runs, judges stable"
```

The operator identity is captured in the `autonomy_change` wide_event. The
promotion is not retroactive — only new runs start at the new tier.

Eligibility rules (from `autonomy/transitions.py`):

- ≥100 consecutive judgments with `passed=true`
- ≥0.97 pass rate over the last 200 judgments
- ≥72h span between first and last
- No harm verdict in the past 168h
- Target tier ≤ T4
- Target tier must not skip more than one level

---

## 9. Hot-reload registry

Three equivalent ways:

```bash
# CLI
acp registry reload

# Signal
kill -HUP $(pgrep -f 'acp serve')

# HTTP (operator surface)
curl -X POST http://localhost:8080/v1/registry/reload
```

**In-flight runs are NOT affected**: each `task_start` pins `agent_version`
and `model_version`. Tier checks use the pinned version. Only *new* runs
pick up the reloaded YAML.

To verify the reload:

```bash
curl http://localhost:8080/v1/agents | jq '.[].version'
```

If a YAML fails validation, the reload is rejected atomically (no partial
state). The error appears in `acp serve` stdout.
