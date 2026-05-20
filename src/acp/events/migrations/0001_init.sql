-- ACP v1.0 initial schema. Source of truth — MASTER_PLAN.md section 3.
--
-- Note: the `agents` table is OWNED by RegistryStore (see acp/registry/store.py).
-- RegistryStore.__init__ creates it via its own DDL with the richer column set
-- (default_tier, budget_hourly_usd, budget_hourly_tok, spec_json, etc.). We
-- intentionally do NOT create a narrower placeholder here — having two DDLs
-- collide caused W4 tests to need workarounds. Single source of truth wins.

CREATE TABLE IF NOT EXISTS wide_events (
  event_id TEXT PRIMARY KEY,
  prev_event_id TEXT,
  ts INTEGER NOT NULL,
  run_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  task_class TEXT NOT NULL,
  model_version TEXT NOT NULL,
  step INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  tool_name TEXT,
  tier_required TEXT,
  outcome TEXT,
  intent TEXT,
  agent_claim TEXT,
  attrs_json TEXT NOT NULL,
  chain_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_run ON wide_events(run_id, step);
CREATE INDEX IF NOT EXISTS ix_events_class_model_ts ON wide_events(task_class, model_version, ts);
CREATE INDEX IF NOT EXISTS ix_events_agent_ts ON wide_events(agent_id, ts);

CREATE TABLE IF NOT EXISTS budgets (
  agent_id TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  tokens INTEGER DEFAULT 0,
  usd_micros INTEGER DEFAULT 0,
  tool_calls INTEGER DEFAULT 0,
  PRIMARY KEY(agent_id, window_start)
);

CREATE TABLE IF NOT EXISTS judgments (
  judgment_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES wide_events(event_id),
  judge_name TEXT NOT NULL,
  judge_model TEXT NOT NULL,
  verdict TEXT NOT NULL,
  rubric_json TEXT NOT NULL,
  rationale TEXT,
  ts INTEGER NOT NULL,
  retroactively_flipped INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_judgments_event ON judgments(event_id);

CREATE TABLE IF NOT EXISTS goodhart_flags (
  flag_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  signal TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS outcome_signals (
  signal_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  value_json TEXT NOT NULL,
  delay_seconds INTEGER NOT NULL,
  source TEXT NOT NULL,
  ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_outcome_run ON outcome_signals(run_id);

CREATE TABLE IF NOT EXISTS slo_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  ts INTEGER NOT NULL,
  agent_id TEXT NOT NULL,
  task_class TEXT NOT NULL,
  model_version TEXT NOT NULL,
  window_label TEXT NOT NULL,
  budget_class TEXT NOT NULL,
  sli_value REAL NOT NULL,
  slo_target REAL NOT NULL,
  burn_rate REAL NOT NULL,
  budget_remaining REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_slo_recent ON slo_snapshots(agent_id, task_class, ts DESC);

CREATE TABLE IF NOT EXISTS autonomy_state (
  agent_id TEXT NOT NULL,
  task_class TEXT NOT NULL,
  current_tier TEXT NOT NULL,
  since INTEGER NOT NULL,
  last_reason TEXT,
  PRIMARY KEY(agent_id, task_class)
);

CREATE TABLE IF NOT EXISTS approvals (
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

CREATE TABLE IF NOT EXISTS audit_queue (
  audit_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  reviewer TEXT,
  reviewed_at INTEGER,
  notes TEXT,
  human_label TEXT
);

CREATE TABLE IF NOT EXISTS calibration (
  cal_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  judge_panel_label TEXT NOT NULL,
  human_label TEXT NOT NULL,
  delta INTEGER NOT NULL,
  judge_model TEXT NOT NULL,
  task_class TEXT NOT NULL,
  ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS action_tokens (
  token_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  args_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  consumed_at INTEGER
);
